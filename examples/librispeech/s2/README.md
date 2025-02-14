# LibriSpeech

## Data
| Data Subset | Duration in Seconds |
| data/manifest.train |  0.83s ~ 29.735s |
| data/manifest.dev | 1.065 ~ 35.155s |  
| data/manifest.test-clean | 1.285s ~ 34.955s |

## Conformer
| Model | Params | Config | Augmentation| Test set | Decode method | Loss | WER |  
| --- | --- | --- | --- | --- | --- | --- | --- |
| conformer | 47.63 M | conf/conformer.yaml | spec_aug + shift | test-clean | attention | - | - |  
| conformer | 47.63 M | conf/conformer.yaml | spec_aug + shift | test-clean | ctc_greedy_search |  |  |  
| conformer | 47.63 M | conf/conformer.yaml | spec_aug + shift | test-clean | ctc_prefix_beam_search |  | |  
| conformer | 47.63 M | conf/conformer.yaml | spec_aug + shift | test-clean | attention_rescoring |  |  |  

### Test w/o length filter
| Model | Params | Config | Augmentation| Test set | Decode method | Loss | WER |  
| --- | --- | --- | --- | --- | --- | --- | --- |
| conformer | 47.63 M | conf/conformer.yaml | spec_aug + shift | test-clean-all | attention |  |  |  


## Chunk Conformer

| Model | Params | Config | Augmentation| Test set | Decode method | Chunk Size & Left Chunks | Loss | WER |  
| --- | --- | --- | --- | --- | --- | --- | --- | --- |  
| conformer | 47.63 M | conf/chunk_conformer.yaml | spec_aug + shift | test-clean | attention | 16, -1 |  |  |  
| conformer | 47.63 M | conf/chunk_conformer.yaml | spec_aug + shift | test-clean | ctc_greedy_search | 16, -1 |  |  |  
| conformer | 47.63 M | conf/chunk_conformer.yaml | spec_aug + shift | test-clean | ctc_prefix_beam_search | 16, -1 |  | - |  
| conformer | 47.63 M | conf/chunk_conformer.yaml | spec_aug + shift | test-clean | attention_rescoring | 16, -1 |  | - |  


## Transformer
| Model | Params | Config | Augmentation| Test set | Decode method | Loss | WER |  
| --- | --- | --- | --- | --- | --- | --- | --- |
| transformer | 32.52 M | conf/transformer.yaml | spec_aug + shift | test-clean | attention |  |  |  

### Test w/o length filter
| Model | Params | Config | Augmentation| Test set | Decode method | Loss | WER |  
| --- | --- | --- | --- | --- | --- | --- | --- |
| transformer | 32.52 M | conf/transformer.yaml | spec_aug + shift | test-clean-all | attention | | |  
