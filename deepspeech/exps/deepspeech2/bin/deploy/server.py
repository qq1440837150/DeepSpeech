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
"""Server-end for the ASR demo."""
import functools

import numpy as np
import paddle
from paddle.io import DataLoader

from deepspeech.exps.deepspeech2.config import get_cfg_defaults
from deepspeech.io.collator import SpeechCollator
from deepspeech.io.dataset import ManifestDataset
from deepspeech.models.ds2 import DeepSpeech2Model
from deepspeech.training.cli import default_argument_parser
from deepspeech.utils.socket_server import AsrRequestHandler
from deepspeech.utils.socket_server import AsrTCPServer
from deepspeech.utils.socket_server import warm_up_test
from deepspeech.utils.utility import add_arguments
from deepspeech.utils.utility import print_arguments


def start_server(config, args):
    """Start the ASR server"""
    config.defrost()
    config.data.manifest = config.data.test_manifest
    dataset = ManifestDataset.from_config(config)

    config.collator.augmentation_config = ""
    config.collator.keep_transcription_text = True
    config.collator.batch_size = 1
    config.collator.num_workers = 0
    collate_fn = SpeechCollator.from_config(config)
    test_loader = DataLoader(dataset, collate_fn=collate_fn, num_workers=0)

    model = DeepSpeech2Model.from_pretrained(test_loader, config,
                                             args.checkpoint_path)
    model.eval()

    # prepare ASR inference handler
    def file_to_transcript(filename):
        feature = test_loader.collate_fn.process_utterance(filename, "")
        audio = np.array([feature[0]]).astype('float32')  #[1, T, D]
        # audio = audio.swapaxes(1,2)
        print('---file_to_transcript feature----')
        print(audio.shape)
        audio_len = feature[0].shape[0]
        print(audio_len)
        audio_len = np.array([audio_len]).astype('int64')  # [1]

        result_transcript = model.decode(
            paddle.to_tensor(audio),
            paddle.to_tensor(audio_len),
            vocab_list=test_loader.collate_fn.vocab_list,
            decoding_method=config.decoding.decoding_method,
            lang_model_path=config.decoding.lang_model_path,
            beam_alpha=config.decoding.alpha,
            beam_beta=config.decoding.beta,
            beam_size=config.decoding.beam_size,
            cutoff_prob=config.decoding.cutoff_prob,
            cutoff_top_n=config.decoding.cutoff_top_n,
            num_processes=config.decoding.num_proc_bsearch)
        return result_transcript[0]

    # warming up with utterrances sampled from Librispeech
    print('-----------------------------------------------------------')
    print('Warming up ...')
    warm_up_test(
        audio_process_handler=file_to_transcript,
        manifest_path=args.warmup_manifest,
        num_test_cases=3)
    print('-----------------------------------------------------------')

    # start the server
    server = AsrTCPServer(
        server_address=(args.host_ip, args.host_port),
        RequestHandlerClass=AsrRequestHandler,
        speech_save_dir=args.speech_save_dir,
        audio_process_handler=file_to_transcript)
    print("ASR Server Started.")
    server.serve_forever()


def main(config, args):
    start_server(config, args)


if __name__ == "__main__":
    parser = default_argument_parser()
    add_arg = functools.partial(add_arguments, argparser=parser)
    # yapf: disable
    add_arg('host_ip',          str,
            'localhost',
            "Server's IP address.")
    add_arg('host_port',        int,    8088,    "Server's IP port.")
    add_arg('speech_save_dir',  str,
            'demo_cache',
            "Directory to save demo audios.")
    add_arg('warmup_manifest', str, None, "Filepath of manifest to warm up.")
    args = parser.parse_args()
    print_arguments(args, globals())

    # https://yaml.org/type/float.html
    config = get_cfg_defaults()
    if args.config:
        config.merge_from_file(args.config)
    if args.opts:
        config.merge_from_list(args.opts)
    config.freeze()
    print(config)

    args.warmup_manifest = config.data.test_manifest
    print_arguments(args, globals())

    if args.dump_config:
        with open(args.dump_config, 'w') as f:
            print(config, file=f)

    main(config, args)
