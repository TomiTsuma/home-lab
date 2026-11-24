#!/bin/sh
# Sequential benchmark matrix (one model on the GPU at a time). Results: /tmp/bench_out/<label>.jsonl
# Order: reference first (proves the graders pass a capable model), then the local configurations.
cd /tmp/bench
export BENCH_STALL=300
run() { echo "######## $1  ($(date +%H:%M:%S))"; python3 run_bench.py --model "$2" --label "$1" $3 $4 --timeout 600 2>&1 | grep -v "InsecureKeyLength\|return self._jws"; }
run F-haiku       bench-haiku-agent     --packs
run D-agent-think bench-q35-agent-think --packs
run B-packs       qwen3.5:9b            --packs
run A-baseline    qwen3.5:9b            ""
run E-gemma4      bench-gemma4-agent    --packs
run C-agent       bench-q35-agent       --packs "--tasks T4,T5,T6,T7,T8"
echo "ALL DONE ($(date +%H:%M:%S))"
