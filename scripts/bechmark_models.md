How to execute

- 단일 테스트:
python scripts/benchmark_models.py \
  --models gemma:2b gemma2:2b gemma3:4b qwen2.5-coder:7b deepseek-r1:7b \
  --files dumpState_1777810572958_payload.json \
  --repeat 1

- 여러파일 테스트:
python scripts/benchmark_models.py \
  --models gemma2:4b gemma2:9b gemma3:4b \
  --files act_dumptstate_payload.json radio_issue_payload.json anr_case_payload.json \
  --repeat 1

- 전체 payload 테스트:
python scripts/benchmark_models.py \
  --models gemma2:4b gemma2:9b gemma3:4b \
  --repeat 1
