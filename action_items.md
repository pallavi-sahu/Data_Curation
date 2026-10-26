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


# native words: reject when foreign words are more than 10% of all words.
# Writes {stem}_native.jsonl and {stem}_foreign.jsonl.
# PROHIBITED_CONTENT goes to the foreign file. Other API failures stay out; rerun with --resume.
python3 scripts/filter_native_vocabulary_jsonl.py \
  --input new_data_batch3/sanskrit/sanskrit_tts_merged.jsonl \
  --language sanskrit --workers 8 --batch-size 2

# duplicate check on the native file (no model).
# Writes {stem}_deduped.jsonl and {stem}_deduped_duplicates.jsonl.
python3 scripts/tts_script_similarity_check.py \
  --input new_data_batch3/sanskrit/sanskrit_tts_merged_native.jsonl

# optional, Bengali only: strip $, « », @, #, * and turn / into a space.
# Does not remove words that are already written in Bengali letters.
# python3 scripts/strip_non_bengali_symbols_jsonl.py \
#   --input new_data_batch3/bengali/final_bengali_new.jsonl

# creating csv file from final dataset for manual review
4. python3 scripts/jsonl_paragraph_to_csv.py --input new_data_batch3/sanskrit/sanskrit_tts_merged_native_deduped.jsonl




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