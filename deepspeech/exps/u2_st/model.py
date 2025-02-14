# Copyright (c) 2021 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Contains U2 model."""
import json
import os
import sys
import time
from collections import defaultdict
from contextlib import nullcontext
from pathlib import Path
from typing import Optional

import numpy as np
import paddle
from paddle import distributed as dist
from paddle.io import DataLoader
from yacs.config import CfgNode

from deepspeech.io.collator_st import KaldiPrePorocessedCollator
from deepspeech.io.collator_st import SpeechCollator
from deepspeech.io.collator_st import TripletKaldiPrePorocessedCollator
from deepspeech.io.collator_st import TripletSpeechCollator
from deepspeech.io.dataset import ManifestDataset
from deepspeech.io.dataset import TripletManifestDataset
from deepspeech.io.sampler import SortagradBatchSampler
from deepspeech.io.sampler import SortagradDistributedBatchSampler
from deepspeech.models.u2_st import U2STModel
from deepspeech.training.gradclip import ClipGradByGlobalNormWithLog
from deepspeech.training.scheduler import WarmupLR
from deepspeech.training.timer import Timer
from deepspeech.training.trainer import Trainer
from deepspeech.utils import bleu_score
from deepspeech.utils import ctc_utils
from deepspeech.utils import layer_tools
from deepspeech.utils import mp_tools
from deepspeech.utils import text_grid
from deepspeech.utils import utility
from deepspeech.utils.log import Log

logger = Log(__name__).getlog()


class U2STTrainer(Trainer):
    @classmethod
    def params(cls, config: Optional[CfgNode]=None) -> CfgNode:
        # training config
        default = CfgNode(
            dict(
                n_epoch=50,  # train epochs
                log_interval=100,  # steps
                accum_grad=1,  # accum grad by # steps
                global_grad_clip=5.0,  # the global norm clip
            ))
        default.optim = 'adam'
        default.optim_conf = CfgNode(
            dict(
                lr=5e-4,  # learning rate
                weight_decay=1e-6,  # the coeff of weight decay
            ))
        default.scheduler = 'warmuplr'
        default.scheduler_conf = CfgNode(
            dict(
                warmup_steps=25000,
                lr_decay=1.0,  # learning rate decay
            ))

        if config is not None:
            config.merge_from_other_cfg(default)
        return default

    def __init__(self, config, args):
        super().__init__(config, args)

    def train_batch(self, batch_index, batch_data, msg):
        train_conf = self.config.training
        start = time.time()
        # forward
        utt, audio, audio_len, text, text_len = batch_data
        if isinstance(text, list) and isinstance(text_len, list):
            # joint training with ASR. Two decoding texts [translation, transcription]
            text, text_transcript = text
            text_len, text_transcript_len = text_len
            loss, st_loss, attention_loss, ctc_loss = self.model(
                audio, audio_len, text, text_len, text_transcript,
                text_transcript_len)
        else:
            loss, st_loss, attention_loss, ctc_loss = self.model(
                audio, audio_len, text, text_len)

        # loss div by `batch_size * accum_grad`
        loss /= train_conf.accum_grad
        losses_np = {'loss': float(loss) * train_conf.accum_grad}
        if attention_loss:
            losses_np['att_loss'] = float(attention_loss)
        if ctc_loss:
            losses_np['ctc_loss'] = float(ctc_loss)

        # loss backward
        if (batch_index + 1) % train_conf.accum_grad != 0:
            # Disable gradient synchronizations across DDP processes.
            # Within this context, gradients will be accumulated on module
            # variables, which will later be synchronized.
            context = self.model.no_sync
        else:
            # Used for single gpu training and DDP gradient synchronization
            # processes.
            context = nullcontext
        with context():
            loss.backward()
            layer_tools.print_grads(self.model, print_func=None)

        # optimizer step
        if (batch_index + 1) % train_conf.accum_grad == 0:
            self.optimizer.step()
            self.optimizer.clear_grad()
            self.lr_scheduler.step()
            self.iteration += 1

        iteration_time = time.time() - start

        if (batch_index + 1) % train_conf.log_interval == 0:
            msg += "train time: {:>.3f}s, ".format(iteration_time)
            msg += "batch size: {}, ".format(self.config.collator.batch_size)
            msg += "accum: {}, ".format(train_conf.accum_grad)
            msg += ', '.join('{}: {:>.6f}'.format(k, v)
                             for k, v in losses_np.items())
            logger.info(msg)

            if dist.get_rank() == 0 and self.visualizer:
                losses_np_v = losses_np.copy()
                losses_np_v.update({"lr": self.lr_scheduler()})
                self.visualizer.add_scalars("step", losses_np_v,
                                            self.iteration - 1)

    @paddle.no_grad()
    def valid(self):
        self.model.eval()
        logger.info(f"Valid Total Examples: {len(self.valid_loader.dataset)}")
        valid_losses = defaultdict(list)
        num_seen_utts = 1
        total_loss = 0.0
        for i, batch in enumerate(self.valid_loader):
            utt, audio, audio_len, text, text_len = batch
            if isinstance(text, list) and isinstance(text_len, list):
                text, text_transcript = text
                text_len, text_transcript_len = text_len
                loss, st_loss, attention_loss, ctc_loss = self.model(
                    audio, audio_len, text, text_len, text_transcript,
                    text_transcript_len)
            else:
                loss, st_loss, attention_loss, ctc_loss = self.model(
                    audio, audio_len, text, text_len)
            if paddle.isfinite(loss):
                num_utts = batch[1].shape[0]
                num_seen_utts += num_utts
                total_loss += float(st_loss) * num_utts
                valid_losses['val_loss'].append(float(st_loss))
                if attention_loss:
                    valid_losses['val_att_loss'].append(float(attention_loss))
                if ctc_loss:
                    valid_losses['val_ctc_loss'].append(float(ctc_loss))

            if (i + 1) % self.config.training.log_interval == 0:
                valid_dump = {k: np.mean(v) for k, v in valid_losses.items()}
                valid_dump['val_history_st_loss'] = total_loss / num_seen_utts

                # logging
                msg = f"Valid: Rank: {dist.get_rank()}, "
                msg += "epoch: {}, ".format(self.epoch)
                msg += "step: {}, ".format(self.iteration)
                msg += "batch: {}/{}, ".format(i + 1, len(self.valid_loader))
                msg += ', '.join('{}: {:>.6f}'.format(k, v)
                                 for k, v in valid_dump.items())
                logger.info(msg)

        logger.info('Rank {} Val info st_val_loss {}'.format(
            dist.get_rank(), total_loss / num_seen_utts))
        return total_loss, num_seen_utts

    def train(self):
        """The training process control by step."""
        # !!!IMPORTANT!!!
        # Try to export the model by script, if fails, we should refine
        # the code to satisfy the script export requirements
        # script_model = paddle.jit.to_static(self.model)
        # script_model_path = str(self.checkpoint_dir / 'init')
        # paddle.jit.save(script_model, script_model_path)

        from_scratch = self.resume_or_scratch()
        if from_scratch:
            # save init model, i.e. 0 epoch
            self.save(tag='init')

        self.lr_scheduler.step(self.iteration)
        if self.parallel:
            self.train_loader.batch_sampler.set_epoch(self.epoch)

        logger.info(f"Train Total Examples: {len(self.train_loader.dataset)}")
        while self.epoch < self.config.training.n_epoch:
            with Timer("Epoch-Train Time Cost: {}"):
                self.model.train()
                try:
                    data_start_time = time.time()
                    for batch_index, batch in enumerate(self.train_loader):
                        dataload_time = time.time() - data_start_time
                        msg = "Train: Rank: {}, ".format(dist.get_rank())
                        msg += "epoch: {}, ".format(self.epoch)
                        msg += "step: {}, ".format(self.iteration)
                        msg += "batch : {}/{}, ".format(batch_index + 1,
                                                        len(self.train_loader))
                        msg += "lr: {:>.8f}, ".format(self.lr_scheduler())
                        msg += "data time: {:>.3f}s, ".format(dataload_time)
                        self.train_batch(batch_index, batch, msg)
                        self.after_train_batch()
                        data_start_time = time.time()
                except Exception as e:
                    logger.error(e)
                    raise e

            with Timer("Eval Time Cost: {}"):
                total_loss, num_seen_utts = self.valid()
                if dist.get_world_size() > 1:
                    num_seen_utts = paddle.to_tensor(num_seen_utts)
                    # the default operator in all_reduce function is sum.
                    dist.all_reduce(num_seen_utts)
                    total_loss = paddle.to_tensor(total_loss)
                    dist.all_reduce(total_loss)
                    cv_loss = total_loss / num_seen_utts
                    cv_loss = float(cv_loss)
                else:
                    cv_loss = total_loss / num_seen_utts

            logger.info(
                'Epoch {} Val info val_loss {}'.format(self.epoch, cv_loss))
            if self.visualizer:
                self.visualizer.add_scalars(
                    'epoch', {'cv_loss': cv_loss,
                              'lr': self.lr_scheduler()}, self.epoch)
            self.save(tag=self.epoch, infos={'val_loss': cv_loss})
            self.new_epoch()

    def setup_dataloader(self):
        config = self.config.clone()
        config.defrost()
        config.collator.keep_transcription_text = False

        # train/valid dataset, return token ids
        Dataset = TripletManifestDataset if config.model.model_conf.asr_weight > 0. else ManifestDataset
        config.data.manifest = config.data.train_manifest
        train_dataset = Dataset.from_config(config)

        config.data.manifest = config.data.dev_manifest
        dev_dataset = Dataset.from_config(config)

        if config.collator.raw_wav:
            if config.model.model_conf.asr_weight > 0.:
                Collator = TripletSpeechCollator
                TestCollator = SpeechCollator
            else:
                TestCollator = Collator = SpeechCollator
            # Not yet implement the mtl loader for raw_wav.
        else:
            if config.model.model_conf.asr_weight > 0.:
                Collator = TripletKaldiPrePorocessedCollator
                TestCollator = KaldiPrePorocessedCollator
            else:
                TestCollator = Collator = KaldiPrePorocessedCollator

        collate_fn_train = Collator.from_config(config)

        config.collator.augmentation_config = ""
        collate_fn_dev = Collator.from_config(config)

        if self.parallel:
            batch_sampler = SortagradDistributedBatchSampler(
                train_dataset,
                batch_size=config.collator.batch_size,
                num_replicas=None,
                rank=None,
                shuffle=True,
                drop_last=True,
                sortagrad=config.collator.sortagrad,
                shuffle_method=config.collator.shuffle_method)
        else:
            batch_sampler = SortagradBatchSampler(
                train_dataset,
                shuffle=True,
                batch_size=config.collator.batch_size,
                drop_last=True,
                sortagrad=config.collator.sortagrad,
                shuffle_method=config.collator.shuffle_method)
        self.train_loader = DataLoader(
            train_dataset,
            batch_sampler=batch_sampler,
            collate_fn=collate_fn_train,
            num_workers=config.collator.num_workers, )
        self.valid_loader = DataLoader(
            dev_dataset,
            batch_size=config.collator.batch_size,
            shuffle=False,
            drop_last=False,
            collate_fn=collate_fn_dev)

        # test dataset, return raw text
        config.data.manifest = config.data.test_manifest
        # filter test examples, will cause less examples, but no mismatch with training
        # and can use large batch size , save training time, so filter test egs now.
        # config.data.min_input_len = 0.0  # second
        # config.data.max_input_len = float('inf')  # second
        # config.data.min_output_len = 0.0  # tokens
        # config.data.max_output_len = float('inf')  # tokens
        # config.data.min_output_input_ratio = 0.00
        # config.data.max_output_input_ratio = float('inf')
        test_dataset = ManifestDataset.from_config(config)
        # return text ord id
        config.collator.keep_transcription_text = True
        config.collator.augmentation_config = ""
        self.test_loader = DataLoader(
            test_dataset,
            batch_size=config.decoding.batch_size,
            shuffle=False,
            drop_last=False,
            collate_fn=TestCollator.from_config(config))
        # return text token id
        config.collator.keep_transcription_text = False
        self.align_loader = DataLoader(
            test_dataset,
            batch_size=config.decoding.batch_size,
            shuffle=False,
            drop_last=False,
            collate_fn=TestCollator.from_config(config))
        logger.info("Setup train/valid/test/align Dataloader!")

    def setup_model(self):
        config = self.config
        model_conf = config.model
        model_conf.defrost()
        model_conf.input_dim = self.train_loader.collate_fn.feature_size
        model_conf.output_dim = self.train_loader.collate_fn.vocab_size
        model_conf.freeze()
        model = U2STModel.from_config(model_conf)

        if self.parallel:
            model = paddle.DataParallel(model)

        logger.info(f"{model}")
        layer_tools.print_params(model, logger.info)

        train_config = config.training
        optim_type = train_config.optim
        optim_conf = train_config.optim_conf
        scheduler_type = train_config.scheduler
        scheduler_conf = train_config.scheduler_conf

        if scheduler_type == 'expdecaylr':
            lr_scheduler = paddle.optimizer.lr.ExponentialDecay(
                learning_rate=optim_conf.lr,
                gamma=scheduler_conf.lr_decay,
                verbose=False)
        elif scheduler_type == 'warmuplr':
            lr_scheduler = WarmupLR(
                learning_rate=optim_conf.lr,
                warmup_steps=scheduler_conf.warmup_steps,
                verbose=False)
        elif scheduler_type == 'noam':
            lr_scheduler = paddle.optimizer.lr.NoamDecay(
                learning_rate=optim_conf.lr,
                d_model=model_conf.encoder_conf.output_size,
                warmup_steps=scheduler_conf.warmup_steps,
                verbose=False)
        else:
            raise ValueError(f"Not support scheduler: {scheduler_type}")

        grad_clip = ClipGradByGlobalNormWithLog(train_config.global_grad_clip)
        weight_decay = paddle.regularizer.L2Decay(optim_conf.weight_decay)
        if optim_type == 'adam':
            optimizer = paddle.optimizer.Adam(
                learning_rate=lr_scheduler,
                parameters=model.parameters(),
                weight_decay=weight_decay,
                grad_clip=grad_clip)
        else:
            raise ValueError(f"Not support optim: {optim_type}")

        self.model = model
        self.optimizer = optimizer
        self.lr_scheduler = lr_scheduler
        logger.info("Setup model/optimizer/lr_scheduler!")


class U2STTester(U2STTrainer):
    @classmethod
    def params(cls, config: Optional[CfgNode]=None) -> CfgNode:
        # decoding config
        default = CfgNode(
            dict(
                alpha=2.5,  # Coef of LM for beam search.
                beta=0.3,  # Coef of WC for beam search.
                cutoff_prob=1.0,  # Cutoff probability for pruning.
                cutoff_top_n=40,  # Cutoff number for pruning.
                lang_model_path='models/lm/common_crawl_00.prune01111.trie.klm',  # Filepath for language model.
                decoding_method='attention',  # Decoding method. Options: 'attention', 'ctc_greedy_search',
                # 'ctc_prefix_beam_search', 'attention_rescoring'
                error_rate_type='bleu',  # Error rate type for evaluation. Options `bleu`, 'char_bleu'
                num_proc_bsearch=8,  # # of CPUs for beam search.
                beam_size=10,  # Beam search width.
                batch_size=16,  # decoding batch size
                ctc_weight=0.0,  # ctc weight for attention rescoring decode mode.
                decoding_chunk_size=-1,  # decoding chunk size. Defaults to -1.
                # <0: for decoding, use full chunk.
                # >0: for decoding, use fixed chunk size as set.
                # 0: used for training, it's prohibited here.
                num_decoding_left_chunks=-1,  # number of left chunks for decoding. Defaults to -1.
                simulate_streaming=False,  # simulate streaming inference. Defaults to False.
            ))

        if config is not None:
            config.merge_from_other_cfg(default)
        return default

    def __init__(self, config, args):
        super().__init__(config, args)

    def ordid2token(self, texts, texts_len):
        """ ord() id to chr() chr """
        trans = []
        for text, n in zip(texts, texts_len):
            n = n.numpy().item()
            ids = text[:n]
            trans.append(''.join([chr(i) for i in ids]))
        return trans

    def compute_translation_metrics(self,
                                    utts,
                                    audio,
                                    audio_len,
                                    texts,
                                    texts_len,
                                    bleu_func,
                                    fout=None):
        cfg = self.config.decoding
        len_refs, num_ins = 0, 0

        start_time = time.time()
        text_feature = self.test_loader.collate_fn.text_feature

        refs = [
            "".join(chr(t) for t in text[:text_len])
            for text, text_len in zip(texts, texts_len)
        ]
        # from IPython import embed
        # import os
        # embed()
        # os._exit(0)
        hyps = self.model.decode(
            audio,
            audio_len,
            text_feature=text_feature,
            decoding_method=cfg.decoding_method,
            lang_model_path=cfg.lang_model_path,
            beam_alpha=cfg.alpha,
            beam_beta=cfg.beta,
            beam_size=cfg.beam_size,
            cutoff_prob=cfg.cutoff_prob,
            cutoff_top_n=cfg.cutoff_top_n,
            num_processes=cfg.num_proc_bsearch,
            ctc_weight=cfg.ctc_weight,
            decoding_chunk_size=cfg.decoding_chunk_size,
            num_decoding_left_chunks=cfg.num_decoding_left_chunks,
            simulate_streaming=cfg.simulate_streaming)
        decode_time = time.time() - start_time

        for utt, target, result in zip(utts, refs, hyps):
            len_refs += len(target.split())
            num_ins += 1
            if fout:
                fout.write(utt + " " + result + "\n")
            logger.info("\nReference: %s\nHypothesis: %s" % (target, result))
            logger.info("One example BLEU = %s" %
                        (bleu_func([result], [[target]]).prec_str))

        return dict(
            hyps=hyps,
            refs=refs,
            bleu=bleu_func(hyps, [refs]).score,
            len_refs=len_refs,
            num_ins=num_ins,  # num examples
            num_frames=audio_len.sum().numpy().item(),
            decode_time=decode_time)

    @mp_tools.rank_zero_only
    @paddle.no_grad()
    def test(self):
        assert self.args.result_file
        self.model.eval()
        logger.info(f"Test Total Examples: {len(self.test_loader.dataset)}")

        cfg = self.config.decoding
        bleu_func = bleu_score.char_bleu if cfg.error_rate_type == 'char-bleu' else bleu_score.bleu

        stride_ms = self.test_loader.collate_fn.stride_ms
        hyps, refs = [], []
        len_refs, num_ins = 0, 0
        num_frames = 0.0
        num_time = 0.0
        with open(self.args.result_file, 'w') as fout:
            for i, batch in enumerate(self.test_loader):
                metrics = self.compute_translation_metrics(
                    *batch, bleu_func=bleu_func, fout=fout)
                hyps += metrics['hyps']
                refs += metrics['refs']
                bleu = metrics['bleu']
                num_frames += metrics['num_frames']
                num_time += metrics["decode_time"]
                len_refs += metrics['len_refs']
                num_ins += metrics['num_ins']
                rtf = num_time / (num_frames * stride_ms)
                logger.info("RTF: %f, BELU (%d) = %f" % (rtf, num_ins, bleu))

        rtf = num_time / (num_frames * stride_ms)
        msg = "Test: "
        msg += "epoch: {}, ".format(self.epoch)
        msg += "step: {}, ".format(self.iteration)
        msg += "RTF: {}, ".format(rtf)
        msg += "Test set [%s]: %s" % (len(hyps), str(bleu_func(hyps, [refs])))
        logger.info(msg)
        bleu_meta_path = os.path.splitext(self.args.result_file)[0] + '.bleu'
        err_type_str = "BLEU"
        with open(bleu_meta_path, 'w') as f:
            data = json.dumps({
                "epoch":
                self.epoch,
                "step":
                self.iteration,
                "rtf":
                rtf,
                err_type_str:
                bleu_func(hyps, [refs]).score,
                "dataset_hour": (num_frames * stride_ms) / 1000.0 / 3600.0,
                "process_hour":
                num_time / 1000.0 / 3600.0,
                "num_examples":
                num_ins,
                "decode_method":
                self.config.decoding.decoding_method,
            })
            f.write(data + '\n')

    def run_test(self):
        self.resume_or_scratch()
        try:
            self.test()
        except KeyboardInterrupt:
            sys.exit(-1)

    @paddle.no_grad()
    def align(self):
        if self.config.decoding.batch_size > 1:
            logger.fatal('alignment mode must be running with batch_size == 1')
            sys.exit(1)

        # xxx.align
        assert self.args.result_file and self.args.result_file.endswith(
            '.align')

        self.model.eval()
        logger.info(f"Align Total Examples: {len(self.align_loader.dataset)}")

        stride_ms = self.align_loader.collate_fn.stride_ms
        token_dict = self.align_loader.collate_fn.vocab_list
        with open(self.args.result_file, 'w') as fout:
            # one example in batch
            for i, batch in enumerate(self.align_loader):
                key, feat, feats_length, target, target_length = batch

                # 1. Encoder
                encoder_out, encoder_mask = self.model._forward_encoder(
                    feat, feats_length)  # (B, maxlen, encoder_dim)
                maxlen = encoder_out.size(1)
                ctc_probs = self.model.ctc.log_softmax(
                    encoder_out)  # (1, maxlen, vocab_size)

                # 2. alignment
                ctc_probs = ctc_probs.squeeze(0)
                target = target.squeeze(0)
                alignment = ctc_utils.forced_align(ctc_probs, target)
                logger.info("align ids", key[0], alignment)
                fout.write('{} {}\n'.format(key[0], alignment))

                # 3. gen praat
                # segment alignment
                align_segs = text_grid.segment_alignment(alignment)
                logger.info("align tokens", key[0], align_segs)
                # IntervalTier, List["start end token\n"]
                subsample = utility.get_subsample(self.config)
                tierformat = text_grid.align_to_tierformat(
                    align_segs, subsample, token_dict)
                # write tier
                align_output_path = os.path.join(
                    os.path.dirname(self.args.result_file), "align")
                tier_path = os.path.join(align_output_path, key[0] + ".tier")
                with open(tier_path, 'w') as f:
                    f.writelines(tierformat)
                # write textgrid
                textgrid_path = os.path.join(align_output_path,
                                             key[0] + ".TextGrid")
                second_per_frame = 1. / (1000. /
                                         stride_ms)  # 25ms window, 10ms stride
                second_per_example = (
                    len(alignment) + 1) * subsample * second_per_frame
                text_grid.generate_textgrid(
                    maxtime=second_per_example,
                    intervals=tierformat,
                    output=textgrid_path)

    def run_align(self):
        self.resume_or_scratch()
        try:
            self.align()
        except KeyboardInterrupt:
            sys.exit(-1)

    def load_inferspec(self):
        """infer model and input spec.

        Returns:
            nn.Layer: inference model
            List[paddle.static.InputSpec]: input spec.
        """
        from deepspeech.models.u2 import U2InferModel
        infer_model = U2InferModel.from_pretrained(self.test_loader,
                                                   self.config.model.clone(),
                                                   self.args.checkpoint_path)
        feat_dim = self.test_loader.collate_fn.feature_size
        input_spec = [
            paddle.static.InputSpec(shape=[1, None, feat_dim],
                                    dtype='float32'),  # audio, [B,T,D]
            paddle.static.InputSpec(shape=[1],
                                    dtype='int64'),  # audio_length, [B]
        ]
        return infer_model, input_spec

    def export(self):
        infer_model, input_spec = self.load_inferspec()
        assert isinstance(input_spec, list), type(input_spec)
        infer_model.eval()
        static_model = paddle.jit.to_static(infer_model, input_spec=input_spec)
        logger.info(f"Export code: {static_model.forward.code}")
        paddle.jit.save(static_model, self.args.export_path)

    def run_export(self):
        try:
            self.export()
        except KeyboardInterrupt:
            sys.exit(-1)

    def setup(self):
        """Setup the experiment.
        """
        paddle.set_device(self.args.device)

        self.setup_output_dir()
        self.setup_checkpointer()

        self.setup_dataloader()
        self.setup_model()

        self.iteration = 0
        self.epoch = 0

    def setup_output_dir(self):
        """Create a directory used for output.
        """
        # output dir
        if self.args.output:
            output_dir = Path(self.args.output).expanduser()
            output_dir.mkdir(parents=True, exist_ok=True)
        else:
            output_dir = Path(
                self.args.checkpoint_path).expanduser().parent.parent
            output_dir.mkdir(parents=True, exist_ok=True)

        self.output_dir = output_dir
