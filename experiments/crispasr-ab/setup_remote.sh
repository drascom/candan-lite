#!/usr/bin/env bash
set -euo pipefail

experiment_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_dir="$experiment_dir/vendor/CrispASR"

if [[ ! -d "$source_dir/.git" ]]; then
  mkdir -p "$experiment_dir/vendor"
  git clone --depth=1 --recurse-submodules https://github.com/CrispStrobe/CrispASR.git "$source_dir"
else
  git -C "$source_dir" submodule update --init --recursive
fi

patch_file="$experiment_dir/patches/0001-websocket-use-configured-language.patch"
if git -C "$source_dir" apply --reverse --check "$patch_file" >/dev/null 2>&1; then
  : # Daha önce uygulanmış.
elif git -C "$source_dir" apply --check "$patch_file"; then
  git -C "$source_dir" apply "$patch_file"
else
  echo "CrispASR WebSocket dil yaması uygulanamadı." >&2
  exit 1
fi

cuda_home="${CUDA_HOME:-/usr/local/cuda}"
nvcc="${cuda_home}/bin/nvcc"
build_dir="$source_dir/build"
cuda_option=OFF
if [[ -x "$nvcc" ]]; then
  cuda_option=ON
  build_dir="$source_dir/build-cuda"
elif command -v nvcc >/dev/null 2>&1; then
  nvcc="$(command -v nvcc)"
  cuda_option=ON
  build_dir="$source_dir/build-cuda"
else
  echo "nvcc yok: CrispASR CPU deneme ikilisi derleniyor (GPU derlemesi atlandı)." >&2
fi

cmake_args=(-S "$source_dir" -B "$build_dir" -G Ninja -DGGML_CUDA="$cuda_option")
if [[ "$cuda_option" == ON ]]; then
  cmake_args+=(-DCMAKE_CUDA_COMPILER="$nvcc")
fi
cmake "${cmake_args[@]}"
cmake --build "$build_dir" --target crispasr -j"$(nproc)"

echo "Hazır: $build_dir/bin/crispasr"
