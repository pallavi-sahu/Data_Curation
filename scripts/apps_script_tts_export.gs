/**
 * TTS validated-sheet export
 * Paste into: Extensions → Apps Script (bound to the spreadsheet)
 *
 * On each run you are prompted for:
 *   language code (JSON), locale (filename), Drive folder ID/URL
 *
 * Inside the Drive folder this run writes:
 *   {tab}_export_{yyyy-MM-dd_HHmmss}.json
 *   {locale}_files/                         ← new folder each run (Drive allows duplicate names)
 *     batch2_{locale}_script_NNNN.txt
 *   {locale}_files.tgz                      ← flat .txt entries (no nested folder when extracted)
 *
 * process_batch is fixed below (same for all exports).
 *
 * Sheet: header row is auto-detected (must include Script, Edited Script,
 * Did you Edit, QA Status). Data is the rows below that header.
 * Script adds Injected? / Unique script IDs columns if missing.
 */

/** Same for every datapoint / language — change here if needed. */
var PROCESS_BATCH = 'NRB0001';

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Export TTS')
    .addItem('Export active tab', 'exportActiveTab')
    .addToUi();
}

function exportActiveTab() {
  var ui = SpreadsheetApp.getUi();
  var sheet = SpreadsheetApp.getActiveSheet();

  var config = promptConfig_(ui);
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

  var layout = detectHeaderRow_(sheet);
  if (layout.error) {
    ui.alert(layout.error);
    return;
  }

  var headerInfo = ensureTrackingColumns_(sheet, layout.headerRow);
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

  var nextIndex = getNextIndex_(values, headerInfo.col.idMapping, config.filePrefix);
  var exported = [];
  var tarEntries = [];
  var writeFailed = false;
  var filesFolder = null;
  var filesFolderName = config.locale + '_files';

  for (var i = 0; i < values.length; i++) {
    var row = values[i];
    var qa = normalize_(row[headerInfo.col.qaStatus]);
    var injected = normalize_(row[headerInfo.col.injected]);
    if (qa !== 'accept' || (injected !== '' && injected !== 'no' && injected !== 'false')) {
      continue;
    }

    var didEdit = normalize_(row[headerInfo.col.didYouEdit]) === 'yes';
    var text = String(
      didEdit ? row[headerInfo.col.editedScript] : row[headerInfo.col.script]
    ).trim();
    if (!text) {
      continue;
    }

    var stem = config.filePrefix + '_' + pad4_(nextIndex);
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

    sheet.getRange(sheetRow, headerInfo.col.injected + 1).setValue('yes');
    sheet.getRange(sheetRow, headerInfo.col.idMapping + 1).setValue(stem);

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

    nextIndex += 1;
  }

  if (exported.length === 0) {
    if (!writeFailed) {
      ui.alert('No eligible rows (QA Status=ACCEPT, Injected? blank, non-empty text).');
    }
    return;
  }

  var stamp = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd_HHmmss');
  var jsonName = sheet.getName() + '_export_' + stamp + '.json';
  var jsonBody = JSON.stringify(exported, null, 2);
  parentFolder.createFile(jsonName, jsonBody, MimeType.PLAIN_TEXT);

  var tgzName = filesFolderName + '.tgz';
  try {
    parentFolder.createFile(buildFlatTgz_(tarEntries, tgzName));
  } catch (tgzErr) {
    ui.alert(
      'Exported ' + exported.length + ' script(s) and JSON, but failed creating ' + tgzName + ':\n' + tgzErr
    );
    return;
  }

  ui.alert(
    'Exported ' + exported.length + ' script(s).\n' +
    'JSON: ' + jsonName + '\n' +
    'TXT folder: ' + filesFolderName + '\n' +
    'Archive: ' + tgzName + ' (flat — extract puts .txt in current dir)\n' +
    'Parent folder: ' + parentFolder.getName()
  );
}

/**
 * Prompt for language code, locale (filename), and Drive folder.
 * Remembers last answers in DocumentProperties for the next run.
 */
function promptConfig_(ui) {
  var props = PropertiesService.getDocumentProperties();
  var defaults = {
    language: props.getProperty('tts_language') || '',
    locale: props.getProperty('tts_locale') || '',
    folderId: pickDefaultFolder_(props.getProperty('tts_folderId'))
  };

  var language = ask_(ui, 'Language code for JSON (e.g. as, mai)', defaults.language);
  if (language === null) return { error: 'cancelled' };

  var locale = ask_(
    ui,
    'Locale name for filenames (e.g. assamese → batch2_assamese_script_0001)',
    defaults.locale
  );
  if (locale === null) return { error: 'cancelled' };

  var folderRaw = ask_(ui, 'Drive folder ID or full folder URL', defaults.folderId);
  if (folderRaw === null) return { error: 'cancelled' };

  var folderId = extractFolderId_(folderRaw);
  language = String(language || '').trim();
  locale = String(locale || '').trim().toLowerCase().replace(/\s+/g, '_');

  if (!language) return { error: 'Language code is required' };
  if (!locale) return { error: 'Locale is required' };
  if (!folderId) return { error: 'Drive folder ID is required' };

  var filePrefix = 'batch2_' + locale + '_script';

  props.setProperties({
    tts_language: language,
    tts_locale: locale,
    tts_folderId: folderId
  });

  return {
    language: language,
    locale: locale,
    filePrefix: filePrefix,
    processBatch: PROCESS_BATCH,
    folderId: folderId
  };
}

function ask_(ui, title, defaultValue) {
  var result = ui.prompt(title, defaultValue || '', ui.ButtonSet.OK_CANCEL);
  if (result.getSelectedButton() !== ui.Button.OK) {
    return null;
  }
  return String(result.getResponseText()).trim();
}

/** Prefer last saved folder id only if it looks like a real Drive id. */
function pickDefaultFolder_(savedValue) {
  var v = String(savedValue == null ? '' : savedValue).trim();
  if (!v) return '';
  return looksLikeFolderId_(v) ? extractFolderId_(v) : '';
}

function looksLikeFolderId_(v) {
  var id = extractFolderId_(v);
  return id.length >= 20 && !/^\d+$/.test(id);
}

function extractFolderId_(raw) {
  var s = String(raw || '').trim();
  var m = s.match(/\/folders\/([a-zA-Z0-9_-]+)/);
  if (m) return m[1];
  m = s.match(/[?&]id=([a-zA-Z0-9_-]+)/);
  if (m) return m[1];
  return s;
}

/**
 * Build a .tgz whose members are bare filenames only (no directory prefix).
 * entries: [{ name: 'file.txt', content: '...' }, ...]
 */
function buildFlatTgz_(entries, tgzName) {
  var chunks = [];
  for (var i = 0; i < entries.length; i++) {
    var name = entries[i].name;
    if (name.indexOf('/') !== -1 || name.indexOf('\\') !== -1) {
      throw new Error('Archive entry must be a bare filename, got: ' + name);
    }
    var data = Utilities.newBlob(String(entries[i].content), MimeType.PLAIN_TEXT, name).getBytes();
    chunks.push(tarHeaderBytes_(name, data.length));
    chunks.push(data);
    var pad = (512 - (data.length % 512)) % 512;
    if (pad > 0) {
      chunks.push(zeroBytes_(pad));
    }
  }
  // Two 512-byte zero blocks end a tar archive.
  chunks.push(zeroBytes_(1024));

  var tarBlob = Utilities.newBlob(concatBytes_(chunks), 'application/x-tar', 'archive.tar');
  var gzBlob = Utilities.gzip(tarBlob);
  gzBlob.setName(tgzName);
  return gzBlob;
}

/** POSIX ustar header for a regular file; name must be <= 100 chars. */
function tarHeaderBytes_(fileName, size) {
  if (fileName.length > 100) {
    throw new Error('Filename too long for tar header (max 100): ' + fileName);
  }

  var header = zeroBytes_(512);
  writeAscii_(header, 0, fileName, 100);
  writeAscii_(header, 100, '0000644', 8);
  writeAscii_(header, 108, '0000000', 8);
  writeAscii_(header, 116, '0000000', 8);
  writeAscii_(header, 124, padOctal_(size, 11), 12);
  writeAscii_(header, 136, padOctal_(Math.floor(Date.now() / 1000), 11), 12);
  // Checksum field temporarily spaces (ustar rule).
  writeAscii_(header, 148, '        ', 8);
  header[156] = '0'.charCodeAt(0); // regular file
  writeAscii_(header, 257, 'ustar', 6);
  writeAscii_(header, 263, '00', 2);

  var sum = 0;
  for (var i = 0; i < 512; i++) {
    sum += header[i] & 0xff;
  }
  writeAscii_(header, 148, padOctal_(sum, 6) + '\0 ', 8);

  return header;
}

function writeAscii_(arr, offset, str, maxLen) {
  var s = String(str);
  for (var i = 0; i < maxLen; i++) {
    arr[offset + i] = i < s.length ? s.charCodeAt(i) & 0xff : 0;
  }
}

function padOctal_(n, width) {
  var s = (Number(n) >>> 0).toString(8);
  while (s.length < width) s = '0' + s;
  return s;
}

function zeroBytes_(n) {
  var a = [];
  for (var i = 0; i < n; i++) a.push(0);
  return a;
}

function concatBytes_(chunks) {
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

/** Find header row containing Script + QA Status (scan first 20 rows). */
function detectHeaderRow_(sheet) {
  var maxScan = Math.min(20, sheet.getLastRow() || 1);
  for (var r = 1; r <= maxScan; r++) {
    var map = headerMap_(sheet, r);
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

function ensureTrackingColumns_(sheet, headerRow) {
  var lastCol = Math.max(sheet.getLastColumn(), 1);
  var map = headerMap_(sheet, headerRow);

  var required = {
    script: 'script',
    'edited script': 'editedScript',
    'did you edit': 'didYouEdit',
    'qa status': 'qaStatus'
  };

  var col = {};
  for (var req in required) {
    if (map[req] === undefined) {
      return { error: 'Missing required header: ' + req };
    }
    col[required[req]] = map[req];
  }

  var injectedCol = firstHeaderCol_(map, ['injected']);
  if (injectedCol === undefined) {
    lastCol += 1;
    sheet.getRange(headerRow, lastCol).setValue('Injected?');
    injectedCol = lastCol - 1;
  }

  var idCol = firstHeaderCol_(map, ['unique script ids']);
  if (idCol === undefined) {
    lastCol += 1;
    sheet.getRange(headerRow, lastCol).setValue('Unique script IDs');
    idCol = lastCol - 1;
  }

  col.injected = injectedCol;
  col.idMapping = idCol;
  return { col: col };
}

function firstHeaderCol_(map, keys) {
  for (var i = 0; i < keys.length; i++) {
    if (map[keys[i]] !== undefined) return map[keys[i]];
  }
  return undefined;
}

/** Header name → column index, keyed by headerKey_ (first match wins). */
function headerMap_(sheet, headerRow) {
  var lastCol = Math.max(sheet.getLastColumn(), 1);
  var headers = sheet.getRange(headerRow, 1, 1, lastCol).getValues()[0];
  var map = {};
  for (var c = 0; c < headers.length; c++) {
    var key = headerKey_(headers[c]);
    if (key && map[key] === undefined) {
      map[key] = c;
    }
  }
  return map;
}

function getNextIndex_(values, idMappingCol, filePrefix) {
  var max = 0;
  var re = new RegExp('^' + escapeRegExp_(filePrefix) + '_(\\d+)$');
  for (var i = 0; i < values.length; i++) {
    var stem = String(values[i][idMappingCol] || '').trim();
    var m = stem.match(re);
    if (m) {
      var n = parseInt(m[1], 10);
      if (n > max) max = n;
    }
  }
  return max + 1;
}

function normalize_(v) {
  return String(v == null ? '' : v).trim().toLowerCase();
}

/** Header comparison: "Injected?", "QA_Status", "unique script IDs" → letters/spaces only. */
function headerKey_(v) {
  return normalize_(v).replace(/[^a-z0-9]+/g, ' ').trim();
}

function pad4_(n) {
  var s = String(n);
  while (s.length < 4) s = '0' + s;
  return s;
}

function escapeRegExp_(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
