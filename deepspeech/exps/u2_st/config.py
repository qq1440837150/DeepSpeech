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
from yacs.config import CfgNode

from deepspeech.exps.u2_st.model import U2STTester
from deepspeech.exps.u2_st.model import U2STTrainer
from deepspeech.io.collator_st import SpeechCollator
from deepspeech.io.dataset import ManifestDataset
from deepspeech.models.u2_st import U2STModel

_C = CfgNode()

_C.data = ManifestDataset.params()

_C.collator = SpeechCollator.params()

_C.model = U2STModel.params()

_C.training = U2STTrainer.params()

_C.decoding = U2STTester.params()


def get_cfg_defaults():
    """Get a yacs CfgNode object with default values for my_project."""
    # Return a clone so that the defaults will not be altered
    # This is for the "local variable" use pattern
    config = _C.clone()
    config.set_new_allowed(True)
    return config
