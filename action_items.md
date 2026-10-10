# comparing unused scripts with the raw data and extracting unique raw scripts to a jsonl to process it through new validation pipeline
1. python3 scripts/match_unused_scripts_to_raw.py --list unused_scripts/Urdu_expired_unused_scripts.txt --scripts-dir Apollo\ TTS\ dataset/urdu_phase_3/ --raw new_data_batch3/urdu_validated_cleaned.jsonl

# indexing the scripts creating csv from raw data: 
# 1. performing basic cleanup and putting cleaned scripts to a jsonl file
# 2. the paragraph that has number or token that can affect coherence putting them into a csv file for gemini review
2. python3 scripts/clean_tts_new_jsonl.py --input new_data_batch3/santali_validated_cleaned.jsonl --output-dir new_data_batch3/santali/

# creating another csv with verdict using gemini (keep/delete)
3. python scripts/segment_cleanup_verdict.py cleaned_scripts/manipuri_cleaned_tts_new_check.csv

# applying gemini review on the cleaned jsonl
2. python3 scripts/apply_gemini_review_jsonl.py --csv new_data_batch3/santali/santali_cleaned_tts_new_check_verdicts.csv --jsonl new_data_batch3/santali/santali_cleaned_tts_new.jsonl --output new_data_batch3/santali/final_santali_new.jsonl

# merging or splitting two rows of same domain and source to make sure each paragraph has 300-450 words
3. python3 scripts/merge_short_tts_jsonl.py --input new_data_batch3/santali/final_santali_new.jsonl

# creating csv file from final dataset for manual review
4. python3 scripts/jsonl_paragraph_to_csv.py --input new_data_batch3/maithili/maithili_tts_merged.jsonl




######################################################
## Completed scripts

# script level analysis
1. .venv/bin/python scripts/gemini_review_txt_folder.py \
  --folder Apollo_dataset/assamese/ \
  --output completed_scripts/assamese/assamese_script_analysis.csv \
  --workers 8

# mapping with the raw segment and segment level analysis
2. python3 scripts/gemini_review_json_segments.py \
  --folder Apollo_dataset/malayalam \
  --raw new_data_batch3/malayalam_validated_cleaned.jsonl \
  --output completed_scripts/malayalam/malayalam_segments_gemini_review.csv \
  --workers 8

# retrying the failed segment analysis
3. .venv/bin/python scripts/retry_gemini_review_csv.py \
  --csv completed_scripts/assamese/assamese_script_analysis.csv \
  --batch-size 4 --workers 4


  #################

  Apollo TTS dataset: all data
  Apollo dataset: completed ones