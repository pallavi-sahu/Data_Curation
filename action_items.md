write a new script similar to clean_tts_new.jsonl
here alway create new files and keep the original script as it is. in this script

1. index the scripts, give them 1,2,3 ... ids, if spliting one script in multiple then assign them like this 1.1, 1.2.., 
2. remove these things from the scripts, and create another file with <lang>_cleaned_tts_new.jsonl
removal:
```
9. Wikipedia / textbook citation brackets ([1], [ 10 ], [])
19. Page / chapter / lesson IDs (page 12 of 45, பாடம் 4.1, Chapter 3.2)
20. Figure / table / image references (படம் 4.1, Fig. 4.13, Table 2, சித்திரம் 4.9)
21. ISBN / ISSN / DOI strings
22. IP addresses (192.168.1.1)
23. Hex / color codes (#FF5733, RGB(255,0,0))
24. Math operators and expressions (+, −, ×, ÷, =, <, >)
25. Chemical / scientific notation (H₂O, CO₂, 1.5×10⁶, pH 7.4)
26. Hashtags and @mentions (#topic, @user)
27. Copyright / trademark symbols (©, ®, ™)
28. Author–year citations ((Smith, 2020), et al., ibid., pp. 45–67)
29. “See also”, “References”, “citation needed” blocks
30. List-heavy / bullet-heavy paragraphs (•, 1., a), | col | col |)
31. Bank account / card numbers (not in current PII patterns)
32. PIN / postal codes as plain numbers
33. License plates and vehicle registration numbers
34. File paths and extensions without http (.jsonl, en.wikipedia.org/wiki/... as plain text)
35. HTML entities not fixed by ftfy (&nbsp; if ftfy misses them)
36. Kaomoji / ASCII emoticons (:), :-() — emoji only is removed
patterns like  ####, ***, ..., ----, ___, and ..
4. if in a paragraph 1. 2. 3. type of sequence is there then club them into paragraph by removing the numberings, basically if paragraph is written in points
3. remove empty (), [], {}. 
```
3. treat new file <lang>_cleaned_tts_new.jsonl as input and check for these things
```
Latitude / longitude and coordinate blocks (Coordinates: 26°51′N 80°55′E / 26.85°N …)
Degree–minute–second notation (32°05′14″N)
Temperature values and scales (35°C, 98.6°F)
Measurement units with numbers (35 km, 2 cm, 500 kg, m/s, Pa)
Time zones (IST, GMT+5:30, UTC)
Clock times (14:30, 9:00 AM, 9:00 PM)
Durations (2h 30m, 45 min)
Roman numerals (iv, XII, Chapter IV)
Footnote markers (*, †, ‡)
Dates and year ranges (18-06-2024)
Percentages (50%, 12.5 %)
Currency amounts (₹500, Rs. 1,25,000, $10, €20)
Long numeric statistics (23,500, 1,25,000)
Decimals not caught as PII (26.85, 3.14)
Ratios and map scales (1:50000, 2:1)
Version / model numbers (v2.0, iPhone 14, Windows 11)
Ordinal numbers with digits (15th, 3rd, 18 வயது with digit age)
```
create a csv file with fields, id, script, sentece1, sentece2 ....
any number of sentence column can be there, keep the sentences which has any of the above parameter in the sentence. 
ex. in this script
{"source": "https://huggingface.co/datasets/saillab/alpaca-maithili-cleaned", "paragraph": "दुनू गोटे अपन दोस्ती केँ फेर सँ जगौलनि आ नव-नव स्मृति बनौलनि जकरा ओ सभ सदाक लेल संजोगैत रहताह। अंत। पिछला 10 साल मे ग्लोबल वार्मिंग के प्रभाव पर रिपोर्ट तैयार करू ग्लोबल वार्मिंग एगो निर्विवाद आरू जरुरी मुद्दा छै जे हमरऽ ग्रह प॑ बहुत साल स॑ प्रभावित होय रहलऽ छै, जेकरा म॑ पिछला एक दशक भी एकरऽ अपवाद नै छै । पिछला 10 साल स॑ ग्लोबल वार्मिंग केरऽ प्रतिक्रिया हमरऽ पर्यावरण केरऽ विभिन्न पहलू म॑ उल्लेखनीय रूप स॑ देखलऽ गेलऽ छै । ग्लोबल वार्मिंग केरऽ एगो सबसें स्पष्ट प्रभाव औसत वैश्विक तापमान म॑ वृद्धि छै । नासा के अनुसार 2010 स॑ 2019 तलक के साल अब तलक दर्ज करलऽ गेलऽ सबस॑ गर्म दशक छेलै, जेकरा म॑ स॑ हर साल रिकॉर्ड करलऽ गेलऽ १० सबसें गर्म दशक म॑ शामिल छेलै । २०१६ क॑ ई दशक केरऽ सबस॑ गर्म साल के रूप म॑ रेखांकित करलऽ गेलै, जेकरऽ औसत तापमान १९५१-१९८० केरऽ आधार रेखा के औसत स॑ १.०२ डिग्री सेल्सियस अधिक छेलै । बढ़ैत तापमान के कारण विभिन्न क्षेत्र में लंबा समय तक गर्मी के लहर आ सूखा पड़ल अछि, जाहि सं मनुष्य आ वन्यजीव दुनू के जीवन के स्थिति मुश्किल भ गेल अछि. पिछला एक दशक में देखल गेल ग्लोबल वार्मिंग के एकटा आओर प्रभाव अछि दुनिया भर में बर्फ के टोपी आ ग्लेशियर के पिघलब. आर्कटिक म॑ सबसें चरम पिघलना के अनुभव करलऽ गेलऽ छै, जेकरा म॑ सितंबर २०१९ म॑ ई क्षेत्र केरऽ समुद्री बर्फ केरऽ मात्रा सब समय केरऽ निचला स्तर प॑ दर्ज करलऽ गेलऽ छै । ई पिघलना न॑ समुद्र केरऽ तल म॑ वृद्धि (३.३ मिलीमीटर प्रति वर्ष) म॑ काफी योगदान देल॑ छै जे अतीत म॑ देखलऽ गेलऽ छै दशक, जेकरा स॑ आबाद द्वीपीय राष्ट्र आरू निचला तटीय शहरऽ क॑ बाढ़ के खतरा म॑ डाललऽ गेलऽ छै । ग्लोबल वार्मिंग के परिणामस्वरूप जलवायु परिवर्तन के कारण तूफान, तूफान, आरू चक्रवात जैसनऽ प्राकृतिक आपदा के आवृत्ति आरू तीव्रता बढ़ी गेलऽ छै, जेकरऽ समुदायऽ प॑ विनाशकारी प्रभाव पड़ै छै ।"

sentece1: '' पिछला 10 साल मे ग्लोबल वार्मिंग के प्रभाव पर रिपोर्ट तैयार करू ग्लोबल वार्मिंग एगो निर्विवाद आरू जरुरी मुद्दा छै जे हमरऽ ग्रह प॑ बहुत साल स॑ प्रभावित होय रहलऽ छै, जेकरा म॑ पिछला एक दशक भी एकरऽ अपवाद नै छै",
sentece2: "पिछला 10 साल स॑ ग्लोबल वार्मिंग केरऽ प्रतिक्रिया हमरऽ पर्यावरण केरऽ विभिन्न पहलू म॑ उल्लेखनीय रूप स॑ देखलऽ गेलऽ छै ", 

.... same for other sentences

###############################3

3. Defined terminal punctuations for each language to extract the segments with numbers

output:
new_tts_jsonl_file: file with cleanup
dropped_jsonl: list heavy or empty after cleanup 
csv file: input- new_tts_file, output sentence with review categories

phase two:

1. created another csv with columns using gemini:
Language	
script number/ID	
Segments/scripts	
verdict	
why verdict	
character/number category	
if the numerics, numbers are contextual or not	
cleaned segment (retain)	

2. Reviewed it looks good so replaced sentences.segments with cleaned segment (retain) where verdict was `KEEP` and deleted the sentences with verdict `DELETE`.




###############################################

Technical action items:
preprocessing stuff
coherence check, domain, sensitivity
python3 scripts/process_validated_jsonl.py --input new_data_batch3/urdu/urdu.jsonl --output-dir new_data_batch3/urdu --coherence-threshold 0.55 --workers 8



# comparing unused scripts with the raw data and extracting unique raw scripts to a jsonl to process it through new validation pipeline
1. python3 scripts/match_unused_scripts_to_raw.py --list unused_scripts/Urdu_expired_unused_scripts.txt --scripts-dir Apollo\ TTS\ dataset/urdu_phase_3/ --raw new_data_batch3/urdu_validated_cleaned.jsonl

# indexing the scripts creating csv from raw data: 
# 1. performing basic cleanup and putting cleaned scripts to a jsonl file
# 2. the paragraph that has number or token that can affect coherence putting them into a csv file for gemini review
2. python3 scripts/clean_tts_new_jsonl.py --input new_data_batch3/santali_validated_cleaned.jsonl --output-dir new_data_batch3/santali/

# creating another csv with verdict using gemini (keep/delete)
3. python segment_cleanup_verdict.py cleaned_scripts/manipuri_cleaned_tts_new_check.csv

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