/**
 * TTS validated-sheet export — Final Verdict variant
 * Paste into: Extensions → Apps Script as its own project (do not mix
 * with apps_script_tts_export.gs in the same project — function names clash).
 *
 * Same export as apps_script_tts_export.gs, except eligibility uses the
 * Final Verdict column instead of QA Status:
 *   Accept / accept → export
 *   Reject / reject → skip
 *   anything else (blank, other text) → skip
 *
 * On each run you are prompted for:
 *   language code (JSON), locale (filename), Drive folder ID/URL
 *
 * Inside the Drive folder this run writes:
 *   {tab}_export_{yyyy-MM-dd_HHmmss}.json
 *   {locale}_files/                         ← new folder each run (Drive allows duplicate names)
 *     batch3_{locale}_script_NNNN.txt
 *   {locale}_files.tgz                      ← flat .txt entries (no nested folder when extracted)
 *
 * process_batch is fixed below (same for all exports).
 *
 * Sheet: header row is auto-detected (must include Script, Edited Script,
 * Final Verdict). Data is the rows below that header. Export always uses
 * Edited Script (Did you Edit? is ignored). If Edited Script is empty,
 * that row is left unchanged (not exported, not marked Injected).
 * Script adds Injected? / Unique script IDs columns if missing.
 *
 * Large sheets: Apps Script kills any execution at 6 minutes. A run stops
 * itself at MAX_RUNTIME_MS, saves progress, and schedules a trigger that
 * picks up where it left off, repeating until every row is done. The .txt
 * files are written as it goes; the combined JSON and .tgz are produced in
 * a final pass. Progress lives in DocumentProperties, so closing the sheet
 * does not interrupt it.
 */

/** Same for every datapoint / language — change here if needed. */
var PROCESS_BATCH = 'NRB0001';

/** Stop and reschedule this far into a run (hard platform limit is 6 min). */
var MAX_RUNTIME_MS = 4.5 * 60 * 1000;
/** Don't start building the JSON + archive with less than this left. */
var FINALIZE_RESERVE_MS = 2 * 60 * 1000;
/** Rows between progress checkpoints (staging file + sheet write). */
var FLUSH_EVERY_ROWS = 50;
var RESUME_DELAY_MS = 60 * 1000;
/** Fires only if a run is killed before it can reschedule itself. */
var WATCHDOG_DELAY_MS = 7 * 60 * 1000;
/** Long enough that progress toasts overlap instead of leaving blank gaps. */
var PROGRESS_TOAST_SECONDS = 45;

var CONTINUE_HANDLER = 'continueTtsExport';
var STATE_KEY = 'tts_run_state';
var SUMMARY_KEY = 'tts_last_summary';

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Export TTS (Final Verdict)')
    .addItem('Export active tab', 'exportActiveTab')
    .addItem('Resume unfinished export', 'resumeTtsExport')
    .addItem('Cancel unfinished export', 'cancelTtsExport')
    .addToUi();
  showPendingSummary_();
}

function exportActiveTab() {
  var ui = SpreadsheetApp.getUi();

  var existing = loadState_();
  if (existing) {
    var answer = ui.alert(
      'An export is already in progress',
      existing.totalExported + ' script(s) written so far for tab "' + existing.sheetName + '".\n\n' +
        'Yes — resume it now\n' +
        'No — discard it and start a new export (those rows stay marked Injected?=yes, ' +
        'so they will NOT be in the new JSON or archive)\n' +
        'Cancel — do nothing',
      ui.ButtonSet.YES_NO_CANCEL
    );
    if (answer !== ui.Button.YES && answer !== ui.Button.NO) return;
    if (answer === ui.Button.YES) {
      runExportChunk_(true);
      return;
    }
    clearRun_(existing);
  }

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

  if (sheet.getLastRow() < layout.dataStartRow) {
    ui.alert('No data rows found.');
    return;
  }

  var stamp = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd_HHmmss');
  var filesFolderName = config.locale + '_files';

  saveState_({
    phase: 'export',
    ssId: SpreadsheetApp.getActiveSpreadsheet().getId(),
    sheetId: sheet.getSheetId(),
    sheetName: sheet.getName(),
    language: config.language,
    locale: config.locale,
    filePrefix: config.filePrefix,
    processBatch: config.processBatch,
    parentFolderId: config.folderId,
    filesFolderId: parentFolder.createFolder(filesFolderName).getId(),
    filesFolderName: filesFolderName,
    stageFolderId: parentFolder.createFolder('tts_export_staging_' + stamp).getId(),
    stamp: stamp,
    partCount: 0,
    totalExported: 0
  });

  runExportChunk_(true);
}

/** Menu action: continue a run by hand instead of waiting for the trigger. */
function resumeTtsExport() {
  runExportChunk_(true);
}

/** Trigger target. Must stay public — Apps Script cannot call private (_) handlers. */
function continueTtsExport() {
  runExportChunk_(false);
}

function cancelTtsExport() {
  var ui = SpreadsheetApp.getUi();
  var state = loadState_();
  if (!state) {
    ui.alert('No export in progress.');
    return;
  }
  var written = state.totalExported;
  clearRun_(state);
  ui.alert(
    'Cancelled. ' + written + ' .txt file(s) already in ' + state.filesFolderName +
    ' were kept, and those rows stay marked Injected?=yes. No JSON or archive was produced.'
  );
}

/**
 * Export as many rows as fit in one execution, then either reschedule
 * itself or move on to building the JSON + archive.
 */
function runExportChunk_(interactive) {
  var deadline = Date.now() + MAX_RUNTIME_MS;

  var lock = LockService.getDocumentLock();
  if (!lock.tryLock(1000)) {
    notify_(interactive, 'Another export run is still going. Try again in a minute.');
    return;
  }

  try {
    var state = loadState_();
    if (!state) {
      notify_(interactive, 'No export in progress. Use "Export active tab" to start one.');
      return;
    }

    scheduleTrigger_(WATCHDOG_DELAY_MS);

    var ss = SpreadsheetApp.openById(state.ssId);

    if (state.phase === 'finalize') {
      progress_(ss, 'Building the JSON and archive from ' + state.totalExported + ' script(s)…');
      finalize_(state, interactive, ss);
      return;
    }

    var sheet = sheetById_(ss, state.sheetId);
    if (!sheet) {
      halt_(interactive, 'Tab "' + state.sheetName + '" no longer exists. Export paused.');
      return;
    }

    progress_(
      ss,
      state.totalExported
        ? 'Resuming export — ' + state.totalExported + ' script(s) done so far…'
        : 'Starting export…'
    );

    var layout = detectHeaderRow_(sheet);
    if (layout.error) {
      halt_(interactive, layout.error);
      return;
    }

    var headerInfo = ensureTrackingColumns_(sheet, layout.headerRow);
    if (headerInfo.error) {
      halt_(interactive, headerInfo.error);
      return;
    }

    var lastRow = sheet.getLastRow();
    if (lastRow < layout.dataStartRow) {
      state.phase = 'finalize';
      saveState_(state);
      finalize_(state, interactive, ss);
      return;
    }

    var numRows = lastRow - layout.dataStartRow + 1;
    var values = sheet.getRange(layout.dataStartRow, 1, numRows, sheet.getLastColumn()).getValues();
    // Rows finished by earlier chunks already carry Injected?=yes and an ID,
    // so re-reading the sheet is what makes a resumed run pick up correctly.
    var nextIndex = getNextIndex_(values, headerInfo.col.idMapping, state.filePrefix);

    var filesFolder = DriveApp.getFolderById(state.filesFolderId);
    var stageFolder = DriveApp.getFolderById(state.stageFolderId);
    var writer = newSheetWriter_(sheet, layout.dataStartRow, values, headerInfo.col);
    var pending = [];
    var outOfTime = false;

    for (var i = 0; i < values.length; i++) {
      var row = values[i];
      var verdict = normalize_(row[headerInfo.col.finalVerdict]);
      var injected = normalize_(row[headerInfo.col.injected]);
      if (!isAcceptedVerdict_(verdict) || (injected !== '' && injected !== 'no' && injected !== 'false')) {
        continue;
      }

      var text = String(row[headerInfo.col.editedScript] || '').trim();
      if (!text) {
        // Leave the row as-is until Edited Script is filled in.
        continue;
      }

      var stem = state.filePrefix + '_' + pad4_(nextIndex);
      var fileName = stem + '.txt';

      try {
        filesFolder.createFile(fileName, text, MimeType.PLAIN_TEXT);
      } catch (err) {
        checkpoint_(state, stageFolder, pending, writer);
        halt_(
          interactive,
          'Failed writing ' + fileName + ' at sheet row ' + (layout.dataStartRow + i) + ':\n' + err +
            '\n\n' + state.totalExported + ' script(s) exported so far. Fix the problem and use ' +
            '"Resume unfinished export".'
        );
        return;
      }

      writer.mark(i, stem);
      pending.push({ name: fileName, content: text });
      nextIndex += 1;
      state.totalExported += 1;

      if (pending.length >= FLUSH_EVERY_ROWS) {
        checkpoint_(state, stageFolder, pending, writer);
        pending = [];
        progress_(ss, 'Exporting… ' + state.totalExported + ' script(s) written.');
        if (Date.now() > deadline) {
          outOfTime = true;
          break;
        }
      }
    }

    checkpoint_(state, stageFolder, pending, writer);

    if (outOfTime) {
      scheduleTrigger_(RESUME_DELAY_MS);
      notify_(
        interactive,
        'Paused before the 6-minute limit with ' + state.totalExported + ' script(s) exported.\n' +
          'It resumes automatically in about a minute — you can close the sheet.'
      );
      return;
    }

    state.phase = 'finalize';
    saveState_(state);

    if (Date.now() + FINALIZE_RESERVE_MS < deadline) {
      progress_(ss, 'All rows exported. Building the JSON and archive…');
      finalize_(state, interactive, ss);
    } else {
      scheduleTrigger_(RESUME_DELAY_MS);
      notify_(
        interactive,
        'All ' + state.totalExported + ' script(s) exported. Building the JSON and archive in about a minute.'
      );
    }
  } finally {
    lock.releaseLock();
  }
}

/** Build the combined manifest + archive from everything staged across runs. */
function finalize_(state, interactive, ss) {
  var parentFolder = DriveApp.getFolderById(state.parentFolderId);
  var entries = readStagedEntries_(DriveApp.getFolderById(state.stageFolderId));

  if (entries.length === 0) {
    clearRun_(state);
    notify_(interactive, 'No eligible rows (Final Verdict=Accept, Injected? blank, non-empty Edited Script).');
    return;
  }

  var manifest = [];
  for (var i = 0; i < entries.length; i++) {
    manifest.push({
      data: {
        file_name: entries[i].name,
        language: state.language,
        process_batch: state.processBatch
      },
      files: {
        script_file: entries[i].name
      }
    });
  }

  var jsonName = state.sheetName + '_export_' + state.stamp + '.json';
  if (!state.jsonWritten) {
    parentFolder.createFile(jsonName, JSON.stringify(manifest, null, 2), MimeType.PLAIN_TEXT);
    state.jsonWritten = true;
    saveState_(state);
  }

  var tgzName = state.filesFolderName + '.tgz';
  try {
    progress_(ss, 'Compressing ' + entries.length + ' script(s) into ' + tgzName + '…');
    parentFolder.createFile(buildFlatTgz_(entries, tgzName));
  } catch (tgzErr) {
    halt_(
      interactive,
      'Exported ' + entries.length + ' script(s) and JSON, but failed creating ' + tgzName + ':\n' + tgzErr +
        '\n\nUse "Resume unfinished export" to retry the archive.'
    );
    return;
  }

  clearRun_(state);
  notify_(
    interactive,
    'Exported ' + entries.length + ' script(s).\n' +
      'JSON: ' + jsonName + '\n' +
      'TXT folder: ' + state.filesFolderName + '\n' +
      'Archive: ' + tgzName + ' (flat — extract puts .txt in current dir)\n' +
      'Parent folder: ' + parentFolder.getName()
  );
}

/**
 * Persist progress: staged text first, then the sheet. If the execution dies
 * in between, the rows are simply re-exported next run and readStagedEntries_
 * drops the duplicates.
 */
function checkpoint_(state, stageFolder, pending, writer) {
  if (pending.length) {
    state.partCount += 1;
    stageFolder.createFile(
      'part_' + pad6_(state.partCount) + '.json',
      JSON.stringify(pending),
      MimeType.PLAIN_TEXT
    );
  }
  writer.flush();
  saveState_(state);
}

/**
 * Collects Injected? / Unique script IDs updates and writes them as two range
 * writes per checkpoint instead of two API calls per row.
 */
function newSheetWriter_(sheet, dataStartRow, values, col) {
  var injected = [];
  var ids = [];
  for (var i = 0; i < values.length; i++) {
    injected.push([values[i][col.injected]]);
    ids.push([values[i][col.idMapping]]);
  }

  var minDirty = -1;
  var maxDirty = -1;

  return {
    mark: function (i, stem) {
      injected[i] = ['yes'];
      ids[i] = [stem];
      if (minDirty === -1 || i < minDirty) minDirty = i;
      if (i > maxDirty) maxDirty = i;
    },
    flush: function () {
      if (minDirty === -1) return;
      var count = maxDirty - minDirty + 1;
      var startRow = dataStartRow + minDirty;
      sheet.getRange(startRow, col.injected + 1, count, 1)
        .setValues(injected.slice(minDirty, maxDirty + 1));
      sheet.getRange(startRow, col.idMapping + 1, count, 1)
        .setValues(ids.slice(minDirty, maxDirty + 1));
      SpreadsheetApp.flush();
      minDirty = -1;
      maxDirty = -1;
    }
  };
}

/** Staged chunks in write order, first occurrence of each filename wins. */
function readStagedEntries_(stageFolder) {
  var parts = [];
  var files = stageFolder.getFiles();
  while (files.hasNext()) {
    var file = files.next();
    var m = file.getName().match(/^part_(\d+)\.json$/);
    if (m) {
      parts.push({ order: parseInt(m[1], 10), file: file });
    }
  }
  parts.sort(function (a, b) {
    return a.order - b.order;
  });

  var seen = {};
  var entries = [];
  for (var i = 0; i < parts.length; i++) {
    var rows = JSON.parse(parts[i].file.getBlob().getDataAsString('UTF-8'));
    for (var j = 0; j < rows.length; j++) {
      var name = rows[j].name;
      if (seen[name]) continue;
      seen[name] = true;
      entries.push({ name: name, content: rows[j].content });
    }
  }
  return entries;
}

function loadState_() {
  var raw = PropertiesService.getDocumentProperties().getProperty(STATE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch (e) {
    return null;
  }
}

function saveState_(state) {
  PropertiesService.getDocumentProperties().setProperty(STATE_KEY, JSON.stringify(state));
}

function clearRun_(state) {
  deleteContinueTriggers_();
  PropertiesService.getDocumentProperties().deleteProperty(STATE_KEY);
  if (state && state.stageFolderId) {
    try {
      DriveApp.getFolderById(state.stageFolderId).setTrashed(true);
    } catch (e) {
      // Staging folder already gone; nothing to clean up.
    }
  }
}

/** Stop without rescheduling, but keep the state so the user can resume. */
function halt_(interactive, message) {
  deleteContinueTriggers_();
  notify_(interactive, message);
}

function scheduleTrigger_(delayMs) {
  deleteContinueTriggers_();
  ScriptApp.newTrigger(CONTINUE_HANDLER).timeBased().after(delayMs).create();
}

function deleteContinueTriggers_() {
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === CONTINUE_HANDLER) {
      ScriptApp.deleteTrigger(triggers[i]);
    }
  }
}

/**
 * Live progress in the sheet. Google's own "Running script…" banner only
 * appears for menu-initiated runs, so background chunks would otherwise look
 * like nothing is happening.
 */
function progress_(spreadsheet, message) {
  if (!spreadsheet) return;
  try {
    spreadsheet.toast(message, 'TTS export', PROGRESS_TOAST_SECONDS);
  } catch (e) {
    // Nobody has the sheet open; there is nothing to show.
  }
}

/** Trigger runs have no UI, so fall back to a toast plus a message shown on next open. */
function notify_(interactive, message) {
  if (interactive) {
    try {
      SpreadsheetApp.getUi().alert(message);
      return;
    } catch (e) {
      // No UI available; fall through to the toast.
    }
  }
  PropertiesService.getDocumentProperties().setProperty(SUMMARY_KEY, message);
  try {
    SpreadsheetApp.getActiveSpreadsheet().toast(message, 'TTS export', 30);
  } catch (e) {
    // Sheet not open; the message waits in SUMMARY_KEY.
  }
  Logger.log(message);
}

function showPendingSummary_() {
  var props = PropertiesService.getDocumentProperties();
  var summary = props.getProperty(SUMMARY_KEY);
  if (!summary) return;
  props.deleteProperty(SUMMARY_KEY);
  SpreadsheetApp.getActiveSpreadsheet().toast(summary, 'TTS export', 60);
}

function sheetById_(spreadsheet, sheetId) {
  var sheets = spreadsheet.getSheets();
  for (var i = 0; i < sheets.length; i++) {
    if (sheets[i].getSheetId() === sheetId) return sheets[i];
  }
  return null;
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
    'Locale name for filenames (e.g. assamese → batch3_assamese_script_0001)',
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

  var filePrefix = 'batch3_' + locale + '_script';

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

var ZERO_CACHE_ = null;

function zeroBytes_(n) {
  if (!ZERO_CACHE_ || ZERO_CACHE_.length < n) {
    ZERO_CACHE_ = [];
    for (var i = 0; i < n; i++) ZERO_CACHE_.push(0);
  }
  // slice() so callers can mutate their copy (tarHeaderBytes_ does).
  return ZERO_CACHE_.slice(0, n);
}

function concatBytes_(chunks) {
  // push.apply in slices beats per-element assignment, which matters once the
  // archive reaches megabytes.
  var MAX_ARGS = 20000;
  var out = [];
  for (var c = 0; c < chunks.length; c++) {
    var chunk = chunks[c];
    if (chunk.length <= MAX_ARGS) {
      Array.prototype.push.apply(out, chunk);
      continue;
    }
    for (var s = 0; s < chunk.length; s += MAX_ARGS) {
      Array.prototype.push.apply(out, chunk.slice(s, s + MAX_ARGS));
    }
  }
  return out;
}

/** Find header row containing Script + Final Verdict (scan first 20 rows). */
function detectHeaderRow_(sheet) {
  var maxScan = Math.min(20, sheet.getLastRow() || 1);
  for (var r = 1; r <= maxScan; r++) {
    var map = headerMap_(sheet, r);
    if (map['script'] !== undefined && map['final verdict'] !== undefined) {
      return { headerRow: r, dataStartRow: r + 1 };
    }
  }
  return {
    error:
      'Could not find a header row with "Script" and "Final Verdict" in the first 20 rows.\n' +
      'Put those column names on one row and try again.'
  };
}

function ensureTrackingColumns_(sheet, headerRow) {
  var lastCol = Math.max(sheet.getLastColumn(), 1);
  var map = headerMap_(sheet, headerRow);

  var required = {
    script: 'script',
    'edited script': 'editedScript',
    'final verdict': 'finalVerdict'
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

/**
 * Final Verdict: Accept / accept is exported.
 * Reject / reject, and any other value, is skipped.
 */
function isAcceptedVerdict_(verdict) {
  return normalize_(verdict) === 'accept';
}

function normalize_(v) {
  return String(v == null ? '' : v).trim().toLowerCase();
}

/** Header comparison: "Injected?", "Final Verdict", "unique script IDs" → letters/spaces only. */
function headerKey_(v) {
  return normalize_(v).replace(/[^a-z0-9]+/g, ' ').trim();
}

function pad4_(n) {
  var s = String(n);
  while (s.length < 4) s = '0' + s;
  return s;
}

function pad6_(n) {
  var s = String(n);
  while (s.length < 6) s = '0' + s;
  return s;
}

function escapeRegExp_(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
