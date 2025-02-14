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
"""Evaluation for DeepSpeech2 model."""
from deepspeech.exps.deepspeech2.config import get_cfg_defaults
from deepspeech.exps.deepspeech2.model import DeepSpeech2ExportTester as ExportTester
from deepspeech.training.cli import default_argument_parser
from deepspeech.utils.utility import print_arguments


def main_sp(config, args):
    exp = ExportTester(config, args)
    exp.setup()
    exp.run_test()


def main(config, args):
    main_sp(config, args)


if __name__ == "__main__":
    parser = default_argument_parser()
    # save asr result to
    parser.add_argument(
        "--result_file", type=str, help="path of save the asr result")
    #load jit model from
    parser.add_argument(
        "--export_path", type=str, help="path of the jit model to save")
    parser.add_argument("--model_type")
    args = parser.parse_args()
    print_arguments(args, globals())
    if args.model_type is None:
        args.model_type = 'offline'
    print("model_type:{}".format(args.model_type))

    # https://yaml.org/type/float.html
    config = get_cfg_defaults(args.model_type)
    if args.config:
        config.merge_from_file(args.config)
    if args.opts:
        config.merge_from_list(args.opts)
    config.freeze()
    print(config)
    if args.dump_config:
        with open(args.dump_config, 'w') as f:
            print(config, file=f)

    main(config, args)
