#! /usr/bin/env bash

if [ $# != 2 ];then
    echo "usage: ${0} config_path ckpt_path_prefix"
    exit -1
fi

ngpu=$(echo $CUDA_VISIBLE_DEVICES | awk -F "," '{print NF}')
echo "using $ngpu gpus..."

device=gpu
if [ ${ngpu} == 0 ];then
    device=cpu
fi
config_path=$1
ckpt_prefix=$2

for type in fullsentence; do
    echo "decoding ${type}"
    batch_size=32
    python3 -u ${BIN_DIR}/test.py \
    --device ${device} \
    --nproc 1 \
    --config ${config_path} \
    --result_file ${ckpt_prefix}.${type}.rsl \
    --checkpoint_path ${ckpt_prefix} \
    --opts decoding.decoding_method ${type} decoding.batch_size ${batch_size}

    if [ $? -ne 0 ]; then
        echo "Failed in evaluation!"
        exit 1
    fi
done

exit 0
