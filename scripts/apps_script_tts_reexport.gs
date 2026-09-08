/**
 * TTS re-export for rows already ingested (Injected?/Ingested = yes)
 * Paste into: Extensions → Apps Script as its own project (do not mix
 * with apps_script_tts_export.gs in the same project — function names clash).
 *
 * Menu: Reexport TTS → Reexport active tab
 *
 * Eligibility:
 *   QA Status = 1. Accepted / Accepted / accepted / ACCEPTED (same variants as original)
 *   Injected? or Ingested = yes (any case, also "1. yes")
 *   Unique script IDs is non-empty (e.g. batch3_assamese_script_0001)
 *   Reingested is blank (already re-exported rows are skipped)
 *   Did you Edit? is yes / no / blank (any case, also "1. yes" / "1. no")
 *     yes → text from Edited Script
 *     no or blank → text from Script
 *
 * On success, Reingested gets new_{Unique script IDs}
 *   e.g. batch3_assamese_script_0001 → new_batch3_assamese_script_0001
 *
 * Drive outputs (same prompts as original: language, locale, folder):
 *   {tab}_reexport_{yyyy-MM-dd_HHmmss}.json
 *   {locale}_reexport_files/
 *     new_batch3_{locale}_script_NNNN.txt
 *   {locale}_reexport_files.tgz
 */

var REEXPORT_PROCESS_BATCH = 'NRB0001';

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Reexport TTS')
    .addItem('Reexport active tab', 'reexportActiveTab')
    .addToUi();
}

function reexportActiveTab() {
  var ui = SpreadsheetApp.getUi();
  var sheet = SpreadsheetApp.getActiveSheet();

  var config = reexportPromptConfig_(ui);
  if (config.error) {
    if (config.error !== 'cancelled') {
      ui.alert(config.error);
    }
    return;
  }

  var parentFolder;
  try {
    parentFolder = DriveApp.getFolderById(config.folderId);
  } catch (e) {
    ui.alert(
      'Invalid Drive folder ID:\n' + config.folderId +
      '\n\nPaste the ID from:\nhttps://drive.google.com/drive/folders/<ID>\n' +
      '(or paste the full folder URL)'
    );
    return;
  }

  var layout = reexportDetectHeaderRow_(sheet);
  if (layout.error) {
    ui.alert(layout.error);
    return;
  }

  var headerInfo = reexportEnsureTrackingColumns_(sheet, layout.headerRow);
  if (headerInfo.error) {
    ui.alert(headerInfo.error);
    return;
  }

  var lastRow = sheet.getLastRow();
  if (lastRow < layout.dataStartRow) {
    ui.alert('No data rows found.');
    return;
  }

  var numRows = lastRow - layout.dataStartRow + 1;
  var width = sheet.getLastColumn();
  var values = sheet.getRange(layout.dataStartRow, 1, numRows, width).getValues();

  var exported = [];
  var tarEntries = [];
  var writeFailed = false;
  var filesFolder = null;
  var filesFolderName = config.locale + '_reexport_files';
  var ingestedYesCount = 0;

  for (var i = 0; i < values.length; i++) {
    var row = values[i];
    var qa = reexportNormalize_(row[headerInfo.col.qaStatus]);
    var ingested = reexportNormalize_(row[headerInfo.col.ingested]);
    var reingested = String(row[headerInfo.col.reingested] == null ? '' : row[headerInfo.col.reingested]).trim();
    var uniqueId = String(row[headerInfo.col.idMapping] == null ? '' : row[headerInfo.col.idMapping]).trim();

    if (reexportIsYes_(ingested)) ingestedYesCount += 1;

    if (!reexportIsAcceptedQa_(qa)) continue;
    if (!reexportIsYes_(ingested)) continue;
    if (reingested) continue;
    if (!uniqueId) continue;
    if (!reexportIsYesNoOrBlank_(row[headerInfo.col.didYouEdit])) continue;

    var text = reexportResolveText_(row, headerInfo.col);
    if (!text) continue;

    var stem = reexportNewStem_(uniqueId);
    var fileName = stem + '.txt';
    var sheetRow = layout.dataStartRow + i;

    try {
      if (!filesFolder) {
        filesFolder = parentFolder.createFolder(filesFolderName);
      }
      filesFolder.createFile(fileName, text, MimeType.PLAIN_TEXT);
    } catch (err) {
      writeFailed = true;
      ui.alert('Failed writing ' + fileName + ' at sheet row ' + sheetRow + ':\n' + err);
      break;
    }

    sheet.getRange(sheetRow, headerInfo.col.reingested + 1).setValue(stem);

    tarEntries.push({ name: fileName, content: text });

    exported.push({
      data: {
        file_name: fileName,
        language: config.language,
        process_batch: config.processBatch
      },
      files: {
        script_file: fileName
      }
    });
  }

  if (exported.length === 0) {
    if (!writeFailed) {
      if (ingestedYesCount === 0) {
        ui.alert(
          'No ingested rows found.\n' +
          'Need Injected? (or Ingested) = yes on at least one data row.'
        );
      } else {
        ui.alert(
          'Found ' + ingestedYesCount + ' ingested row(s), but none were eligible to reexport.\n' +
          'Need QA Status=accepted, Unique script IDs set, Reingested blank, Did you Edit?=yes/no/blank, and non-empty text.'
        );
      }
    }
    return;
  }

  var stamp = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd_HHmmss');
  var jsonName = sheet.getName() + '_reexport_' + stamp + '.json';
  var jsonBody = JSON.stringify(exported, null, 2);
  parentFolder.createFile(jsonName, jsonBody, MimeType.PLAIN_TEXT);

  var tgzName = filesFolderName + '.tgz';
  try {
    parentFolder.createFile(reexportBuildFlatTgz_(tarEntries, tgzName));
  } catch (tgzErr) {
    ui.alert(
      'Reexported ' + exported.length + ' script(s) and JSON, but failed creating ' + tgzName + ':\n' + tgzErr
    );
    return;
  }

  ui.alert(
    'Reexported ' + exported.length + ' script(s).\n' +
    'JSON: ' + jsonName + '\n' +
    'TXT folder: ' + filesFolderName + '\n' +
    'Archive: ' + tgzName + ' (flat — extract puts .txt in current dir)\n' +
    'Parent folder: ' + parentFolder.getName()
  );
}

function reexportPromptConfig_(ui) {
  var props = PropertiesService.getDocumentProperties();
  var defaults = {
    language: props.getProperty('tts_reexport_language') || props.getProperty('tts_language') || '',
    locale: props.getProperty('tts_reexport_locale') || props.getProperty('tts_locale') || '',
    folderId: reexportPickDefaultFolder_(
      props.getProperty('tts_reexport_folderId') || props.getProperty('tts_folderId')
    )
  };

  var language = reexportAsk_(ui, 'Language code for JSON (e.g. as, mai)', defaults.language);
  if (language === null) return { error: 'cancelled' };

  var locale = reexportAsk_(
    ui,
    'Locale name for folder (e.g. assamese → assamese_reexport_files)',
    defaults.locale
  );
  if (locale === null) return { error: 'cancelled' };

  var folderRaw = reexportAsk_(ui, 'Drive folder ID or full folder URL', defaults.folderId);
  if (folderRaw === null) return { error: 'cancelled' };

  var folderId = reexportExtractFolderId_(folderRaw);
  language = String(language || '').trim();
  locale = String(locale || '').trim().toLowerCase().replace(/\s+/g, '_');

  if (!language) return { error: 'Language code is required' };
  if (!locale) return { error: 'Locale is required' };
  if (!folderId) return { error: 'Drive folder ID is required' };

  props.setProperties({
    tts_reexport_language: language,
    tts_reexport_locale: locale,
    tts_reexport_folderId: folderId
  });

  return {
    language: language,
    locale: locale,
    processBatch: REEXPORT_PROCESS_BATCH,
    folderId: folderId
  };
}

function reexportAsk_(ui, title, defaultValue) {
  var result = ui.prompt(title, defaultValue || '', ui.ButtonSet.OK_CANCEL);
  if (result.getSelectedButton() !== ui.Button.OK) {
    return null;
  }
  return String(result.getResponseText()).trim();
}

function reexportPickDefaultFolder_(savedValue) {
  var v = String(savedValue == null ? '' : savedValue).trim();
  if (!v) return '';
  return reexportLooksLikeFolderId_(v) ? reexportExtractFolderId_(v) : '';
}

function reexportLooksLikeFolderId_(v) {
  var id = reexportExtractFolderId_(v);
  return id.length >= 20 && !/^\d+$/.test(id);
}

function reexportExtractFolderId_(raw) {
  var s = String(raw || '').trim();
  var m = s.match(/\/folders\/([a-zA-Z0-9_-]+)/);
  if (m) return m[1];
  m = s.match(/[?&]id=([a-zA-Z0-9_-]+)/);
  if (m) return m[1];
  return s;
}

function reexportBuildFlatTgz_(entries, tgzName) {
  var chunks = [];
  for (var i = 0; i < entries.length; i++) {
    var name = entries[i].name;
    if (name.indexOf('/') !== -1 || name.indexOf('\\') !== -1) {
      throw new Error('Archive entry must be a bare filename, got: ' + name);
    }
    var data = Utilities.newBlob(String(entries[i].content), MimeType.PLAIN_TEXT, name).getBytes();
    chunks.push(reexportTarHeaderBytes_(name, data.length));
    chunks.push(data);
    var pad = (512 - (data.length % 512)) % 512;
    if (pad > 0) {
      chunks.push(reexportZeroBytes_(pad));
    }
  }
  chunks.push(reexportZeroBytes_(1024));

  var tarBlob = Utilities.newBlob(reexportConcatBytes_(chunks), 'application/x-tar', 'archive.tar');
  var gzBlob = Utilities.gzip(tarBlob);
  gzBlob.setName(tgzName);
  return gzBlob;
}

function reexportTarHeaderBytes_(fileName, size) {
  if (fileName.length > 100) {
    throw new Error('Filename too long for tar header (max 100): ' + fileName);
  }

  var header = reexportZeroBytes_(512);
  reexportWriteAscii_(header, 0, fileName, 100);
  reexportWriteAscii_(header, 100, '0000644', 8);
  reexportWriteAscii_(header, 108, '0000000', 8);
  reexportWriteAscii_(header, 116, '0000000', 8);
  reexportWriteAscii_(header, 124, reexportPadOctal_(size, 11), 12);
  reexportWriteAscii_(header, 136, reexportPadOctal_(Math.floor(Date.now() / 1000), 11), 12);
  reexportWriteAscii_(header, 148, '        ', 8);
  header[156] = '0'.charCodeAt(0);
  reexportWriteAscii_(header, 257, 'ustar', 6);
  reexportWriteAscii_(header, 263, '00', 2);

  var sum = 0;
  for (var i = 0; i < 512; i++) {
    sum += header[i] & 0xff;
  }
  reexportWriteAscii_(header, 148, reexportPadOctal_(sum, 6) + '\0 ', 8);

  return header;
}

function reexportWriteAscii_(arr, offset, str, maxLen) {
  var s = String(str);
  for (var i = 0; i < maxLen; i++) {
    arr[offset + i] = i < s.length ? s.charCodeAt(i) & 0xff : 0;
  }
}

function reexportPadOctal_(n, width) {
  var s = (Number(n) >>> 0).toString(8);
  while (s.length < width) s = '0' + s;
  return s;
}

function reexportZeroBytes_(n) {
  var a = [];
  for (var i = 0; i < n; i++) a.push(0);
  return a;
}

function reexportConcatBytes_(chunks) {
  var total = 0;
  for (var i = 0; i < chunks.length; i++) total += chunks[i].length;
  var out = [];
  out.length = total;
  var pos = 0;
  for (var c = 0; c < chunks.length; c++) {
    var chunk = chunks[c];
    for (var j = 0; j < chunk.length; j++) {
      out[pos++] = chunk[j];
    }
  }
  return out;
}

function reexportDetectHeaderRow_(sheet) {
  var maxScan = Math.min(20, sheet.getLastRow() || 1);
  for (var r = 1; r <= maxScan; r++) {
    var map = reexportHeaderMap_(sheet, r);
    if (map['script'] !== undefined && map['qa status'] !== undefined) {
      return { headerRow: r, dataStartRow: r + 1 };
    }
  }
  return {
    error:
      'Could not find a header row with "Script" and "QA Status" in the first 20 rows.\n' +
      'Put those column names on one row and try again.'
  };
}

function reexportEnsureTrackingColumns_(sheet, headerRow) {
  var lastCol = Math.max(sheet.getLastColumn(), 1);
  var map = reexportHeaderMap_(sheet, headerRow);

  var required = {
    script: 'script',
    'edited script': 'editedScript',
    'qa status': 'qaStatus'
  };

  var col = {};
  for (var req in required) {
    if (map[req] === undefined) {
      return { error: 'Missing required header: ' + req };
    }
    col[required[req]] = map[req];
  }

  var didYouEditCol = reexportFirstHeaderCol_(map, ['did you edit yes no', 'did you edit']);
  if (didYouEditCol === undefined) {
    return { error: 'Missing required header: Did you Edit? (yes/no) or Did you Edit' };
  }
  col.didYouEdit = didYouEditCol;

  var ingestedCol = reexportFirstHeaderCol_(map, ['ingested', 'injected']);
  if (ingestedCol === undefined) {
    return { error: 'Missing required header: Injected? or Ingested' };
  }
  col.ingested = ingestedCol;

  var idCol = reexportFirstHeaderCol_(map, ['unique script ids']);
  if (idCol === undefined) {
    return { error: 'Missing required header: Unique script IDs' };
  }
  col.idMapping = idCol;

  var reingestedCol = reexportFirstHeaderCol_(map, ['reingested']);
  if (reingestedCol === undefined) {
    lastCol += 1;
    sheet.getRange(headerRow, lastCol).setValue('Reingested');
    reingestedCol = lastCol - 1;
  }
  col.reingested = reingestedCol;

  return { col: col };
}

function reexportFirstHeaderCol_(map, keys) {
  for (var i = 0; i < keys.length; i++) {
    if (map[keys[i]] !== undefined) return map[keys[i]];
  }
  return undefined;
}

function reexportHeaderMap_(sheet, headerRow) {
  var lastCol = Math.max(sheet.getLastColumn(), 1);
  var headers = sheet.getRange(headerRow, 1, 1, lastCol).getValues()[0];
  var map = {};
  for (var c = 0; c < headers.length; c++) {
    var key = reexportHeaderKey_(headers[c]);
    if (key && map[key] === undefined) {
      map[key] = c;
    }
  }
  return map;
}

/** yes → Edited Script; no or blank → Script. */
function reexportResolveText_(row, col) {
  var sourceCol = reexportIsYes_(row[col.didYouEdit]) ? col.editedScript : col.script;
  return String(row[sourceCol] == null ? '' : row[sourceCol]).trim();
}

function reexportNewStem_(uniqueId) {
  var stem = uniqueId.replace(/\.txt$/i, '');
  if (/^new_/i.test(stem)) return stem;
  return 'new_' + stem;
}

function reexportIsYesNoOrBlank_(v) {
  var s = reexportNormalize_(v);
  if (!s) return true;
  var letters = s.replace(/[^a-z]/g, '');
  return letters === 'yes' || letters === 'no';
}

function reexportIsYes_(v) {
  var s = reexportNormalize_(v).replace(/[^a-z]/g, '');
  return s === 'yes';
}

function reexportIsAcceptedQa_(qa) {
  var s = reexportNormalize_(qa);
  if (!s) return false;
  if (/^1\s*\.?\s*accepted$/.test(s)) return true;
  if (s.indexOf('1.') === 0 && s.indexOf('accepted') !== -1) return true;
  return s === 'accept' || s === 'accepted';
}

function reexportNormalize_(v) {
  return String(v == null ? '' : v).trim().toLowerCase();
}

function reexportHeaderKey_(v) {
  return reexportNormalize_(v).replace(/[^a-z0-9]+/g, ' ').trim();
}
