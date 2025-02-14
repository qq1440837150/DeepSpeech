#!/bin/bash

# Installation script for Kaldi
#
set -e

apt-get install subversion -y

KALDI_GIT="--depth 1 -b master https://github.com/kaldi-asr/kaldi.git"

KALDI_DIR="$PWD/kaldi"

if [ ! -d "$KALDI_DIR" ]; then
    git clone $KALDI_GIT $KALDI_DIR
else
    echo "$KALDI_DIR already exists!"
fi

cd "$KALDI_DIR/tools"
git pull

# Prevent kaldi from switching default python version
mkdir -p "python"
touch "python/.use_default_python"

./extras/check_dependencies.sh

make -j4

pushd ../src
./configure --shared --use-cuda=no --static-math --mathlib=OPENBLAS --openblas-root=${KALDI_DIR}/../OpenBLAS/install
make clean -j && make depend -j && make -j4
popd

echo "Done installing Kaldi."
