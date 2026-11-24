/**
 * Google Apps Script Webhook for VFX Pipeline Hub (with In-Cell Thumbnail Support)
 * 
 * Setup Instructions:
 * 1. Open your Google Sheet in your web browser.
 * 2. In the top menu, go to: Extensions > Apps Script.
 * 3. Delete any default code in the editor, paste this entire file, and click the Save disk icon.
 * 4. In the function dropdown at the top, select 'authorizeGoogleDrive' and click 'Run'.
 *    - Google will show an 'Authorization Required' dialog.
 *    - Click 'Review permissions' -> choose your account -> click 'Advanced' -> 'Go to VFX Pipeline Hub (unsafe)' -> 'Allow'.
 *    - (This is the one-time step that grants Google Drive access so thumbnails can be saved into your Drive).
 * 5. Click the blue 'Deploy' button (top right) > 'New deployment'.
 *    - If already deployed previously, click: Deploy > Manage deployments > Edit (pencil) > Version: New version > Deploy.
 * 6. Under 'Select type', click the gear icon and choose 'Web app'.
 * 7. Set 'Description': "VFX Pipeline Hub Sync"
 * 8. Set 'Execute as': 'Me'
 * 9. Set 'Who has access': 'Anyone' (so Python can communicate without OAuth token refreshes).
 * 10. Click 'Deploy' and copy the 'Web app URL' (ends in /exec).
 * 11. In VFX Pipeline Hub, click 'Project Setup...' and paste the Web app URL into 'Google Sheet Webhook'.
 */

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("VFX Pipeline")
    .addItem("Authorize Google Drive & Test Folder", "authorizeGoogleDrive")
    .addToUi();
}

/**
 * One-click authorization & diagnostic test function for Google Drive.
 */
function authorizeGoogleDrive() {
  var ui = SpreadsheetApp.getUi();
  try {
    var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
    var meta = readMetadata(sheet);
    var pName = meta.project || "VFX_Project";
    var folder = getOrCreateThumbnailsFolder(pName);
    ui.alert(
      "Google Drive Connected!",
      "Google Drive access is authorized!\n\n" +
      "Thumbnail Folder: " + folder.getName() + "\n" +
      "Folder Link: " + folder.getUrl() + "\n\n" +
      "You can now push thumbnails directly from DaVinci Resolve VFX Pipeline Hub.",
      ui.ButtonSet.OK
    );
  } catch (err) {
    ui.alert(
      "Authorization Required",
      "Google Drive could not be accessed:\n" + err.toString() + "\n\n" +
      "Please run this function from the Apps Script editor once to approve Drive permissions.",
      ui.ButtonSet.OK
    );
  }
}

function doGet(e) {
  var action = (e && e.parameter && e.parameter.action) ? e.parameter.action : "get_meta";
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  
  if (action === "get_meta") {
    var meta = readMetadata(sheet);
    return ContentService.createTextOutput(JSON.stringify(meta)).setMimeType(ContentService.MimeType.JSON);
  } else if (action === "get_all") {
    var data = readAllShots(sheet);
    return ContentService.createTextOutput(JSON.stringify(data)).setMimeType(ContentService.MimeType.JSON);
  } else if (action === "test_drive") {
    try {
      var folder = getOrCreateThumbnailsFolder("VFX_Test");
      return ContentService.createTextOutput(JSON.stringify({
        success: true,
        folder_id: folder.getId(),
        folder_name: folder.getName(),
        folder_url: folder.getUrl()
      })).setMimeType(ContentService.MimeType.JSON);
    } catch (err) {
      return ContentService.createTextOutput(JSON.stringify({
        success: false,
        error: err.toString()
      })).setMimeType(ContentService.MimeType.JSON);
    }
  }
  
  return ContentService.createTextOutput(JSON.stringify({ success: false, error: "Invalid action" })).setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  try {
    var contents = e.postData.contents;
    var payload = JSON.parse(contents);
    var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
    
    if (payload.action === "push") {
      var result = writeShotsAndDashboard(sheet, payload);
      var resp = {
        success: true,
        message: result.driveError ? 
          ("Shots updated, but Google Drive thumbnail error: " + result.driveError) : 
          "Sheet and thumbnails updated successfully",
        last_modified: payload.last_modified,
        thumbnails_uploaded: result.thumbCount || 0
      };
      if (result.driveError) {
        resp.drive_error = result.driveError;
      }
      return ContentService.createTextOutput(JSON.stringify(resp)).setMimeType(ContentService.MimeType.JSON);
    }
    
    return ContentService.createTextOutput(JSON.stringify({ success: false, error: "Unknown action" })).setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ success: false, error: err.toString() })).setMimeType(ContentService.MimeType.JSON);
  }
}

/**
 * Trigger: whenever a collaborator edits any cell, update the 'Last Modified' timestamp automatically.
 */
function onEdit(e) {
  var sheet = e.source.getActiveSheet();
  var row = e.range.getRow();
  // If editing shot rows (row >= 6), update the Last Modified timestamp in B3
  if (row >= 6) {
    var nowStr = Utilities.formatDate(new Date(), "GMT", "yyyy-MM-dd HH:mm:ss");
    sheet.getRange(3, 2).setValue(nowStr);
  }
}

function getOrCreateThumbnailsFolder(projectName) {
  var folderName = "VFX_Thumbnails_" + (projectName || "Project");
  var folders = DriveApp.getFoldersByName(folderName);
  if (folders.hasNext()) {
    return folders.next();
  }
  var newFolder = DriveApp.createFolder(folderName);
  try {
    newFolder.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
  } catch (e) {
    Logger.log("Sharing permission warning: " + e);
  }
  return newFolder;
}

function readMetadata(sheet) {
  var lastRow = sheet.getLastRow();
  if (lastRow < 3) {
    return { success: true, last_modified: "", total_shots: 0, summary: {} };
  }
  
  var project = sheet.getRange(3, 1).getValue().toString();
  var lastModified = sheet.getRange(3, 2).getValue().toString();
  var totalShots = parseInt(sheet.getRange(3, 3).getValue()) || 0;
  var approved = parseInt(sheet.getRange(3, 4).getValue()) || 0;
  var wip = parseInt(sheet.getRange(3, 5).getValue()) || 0;
  var review = parseInt(sheet.getRange(3, 6).getValue()) || 0;
  var pending = parseInt(sheet.getRange(3, 7).getValue()) || 0;
  var omit = parseInt(sheet.getRange(3, 8).getValue()) || 0;
  
  return {
    success: true,
    project: project,
    last_modified: lastModified,
    total_shots: totalShots,
    summary: {
      total: totalShots,
      approved: approved,
      wip: wip,
      review: review,
      pending: pending,
      omit: omit
    }
  };
}

function readAllShots(sheet) {
  var lastRow = sheet.getLastRow();
  var meta = readMetadata(sheet);
  if (lastRow < 6) {
    return { success: true, last_modified: meta.last_modified, project: meta.project, summary: meta.summary, shots: [] };
  }
  
  var headers = sheet.getRange(5, 1, 1, sheet.getLastColumn()).getValues()[0];
  var data = sheet.getRange(6, 1, lastRow - 5, headers.length).getValues();
  var shots = [];
  
  for (var r = 0; r < data.length; r++) {
    var row = data[r];
    var shotObj = {};
    for (var c = 0; c < headers.length; c++) {
      var hName = headers[c];
      if (hName) {
        shotObj[hName] = row[c] !== null && row[c] !== undefined ? row[c].toString().trim() : "";
      }
    }
    if (shotObj["Shot ID"] || shotObj["Clip Name"]) {
      shotObj["shot_id"] = shotObj["Shot ID"] || "";
      shotObj["name"] = shotObj["Clip Name"] || "";
      shotObj["duration"] = shotObj["Duration"] || "";
      shotObj["start_tc"] = shotObj["Start TC"] || "";
      shotObj["end_tc"] = shotObj["End TC"] || "";
      shotObj["status"] = shotObj["Status"] || "Pending";
      shotObj["assign"] = shotObj["Assign"] || "";
      shotObj["difficulty"] = shotObj["Difficulty"] || "";
      shotObj["prior"] = shotObj["Prior"] || "";
      shotObj["notes"] = shotObj["Notes"] || "";
      shotObj["timeline"] = shotObj["Timeline"] || "";
      shotObj["handles"] = shotObj["Handles"] || "10";
      shots.push(shotObj);
    }
  }
  
  return {
    success: true,
    last_modified: meta.last_modified,
    project: meta.project,
    summary: meta.summary,
    shots: shots
  };
}

function writeShotsAndDashboard(sheet, payload) {
  sheet.clear();
  
  var project = payload.project_name || "VFX Project";
  var lastModified = payload.last_modified || Utilities.formatDate(new Date(), "GMT", "yyyy-MM-dd HH:mm:ss");
  var sum = payload.summary || {};
  var headers = payload.headers || [
    "Shot ID", "Clip Name", "Thumbnails", "Duration", "Handles",
    "Start TC", "End TC", "Status", "Assign", "Difficulty", "Prior", "Notes", "Timeline"
  ];
  var shots = payload.shots || [];
  
  // Row 1: Title
  var titleRange = sheet.getRange(1, 1, 1, Math.max(8, headers.length));
  titleRange.merge();
  titleRange.setValue("VFX SHOT BREAKDOWN & DASHBOARD - " + project.toUpperCase());
  titleRange.setBackground("#1e2227").setFontColor("#ffffff").setFontWeight("bold").setFontSize(13).setHorizontalAlignment("center");
  sheet.setRowHeight(1, 32);
  
  // Row 2: KPI Headers
  var kpiHeaders = ["Project", "Last Modified", "Total Shots", "Approved", "WIP", "Review", "Pending", "Omit"];
  var kpiHeaderRange = sheet.getRange(2, 1, 1, 8);
  kpiHeaderRange.setValues([kpiHeaders]);
  kpiHeaderRange.setBackground("#2c313a").setFontColor("#abb2bf").setFontWeight("bold").setFontSize(10).setHorizontalAlignment("center");
  sheet.setRowHeight(2, 22);
  
  // Row 3: KPI Values
  var kpiValues = [
    project,
    lastModified,
    shots.length,
    sum.approved || 0,
    sum.wip || 0,
    sum.review || 0,
    sum.pending || 0,
    sum.omit || 0
  ];
  var kpiValueRange = sheet.getRange(3, 1, 1, 8);
  kpiValueRange.setValues([kpiValues]);
  kpiValueRange.setFontWeight("bold").setFontSize(11).setHorizontalAlignment("center");
  sheet.setRowHeight(3, 26);
  
  // Colors for KPI values
  sheet.getRange(3, 1).setBackground("#3b4048").setFontColor("#ffffff");
  sheet.getRange(3, 2).setBackground("#3b4048").setFontColor("#61afef");
  sheet.getRange(3, 3).setBackground("#21252b").setFontColor("#ffffff");
  sheet.getRange(3, 4).setBackground("#1e3a29").setFontColor("#98c379");
  sheet.getRange(3, 5).setBackground("#3d2f1d").setFontColor("#e5c07b");
  sheet.getRange(3, 6).setBackground("#1c3247").setFontColor("#61afef");
  sheet.getRange(3, 7).setBackground("#282c34").setFontColor("#abb2bf");
  sheet.getRange(3, 8).setBackground("#3d1f24").setFontColor("#e06c75");
  
  // Row 4: Blank
  sheet.setRowHeight(4, 12);
  
  // Row 5: Master Table Headers
  var thRange = sheet.getRange(5, 1, 1, headers.length);
  thRange.setValues([headers]);
  thRange.setBackground("#282c34").setFontColor("#61afef").setFontWeight("bold").setFontSize(10);
  sheet.setRowHeight(5, 26);
  
  // Freeze rows up to header (Row 5)
  sheet.setFrozenRows(5);
  
  // Prepare Thumbnails Folder in Google Drive if any shot has thumbnail data
  var thumbFolder = null;
  var driveErrorMsg = null;
  var thumbCount = 0;
  var hasThumbs = shots.some(function(s) { return s.thumb_b64; });
  if (hasThumbs) {
    try {
      thumbFolder = getOrCreateThumbnailsFolder(project);
    } catch (driveErr) {
      driveErrorMsg = driveErr.toString();
      Logger.log("Drive folder initialization error: " + driveErr);
    }
  }

  // Row 6+: Shot Rows
  if (shots.length > 0) {
    var rowData = [];
    for (var i = 0; i < shots.length; i++) {
      var s = shots[i];
      var row = [];
      for (var h = 0; h < headers.length; h++) {
        var colName = headers[h];
        if (colName === "Shot ID") row.push(s.shot_id || "");
        else if (colName === "Clip Name") row.push(s.name || "");
        else if (colName === "Thumbnails") row.push("");
        else if (colName === "Duration") row.push(s.duration || "");
        else if (colName === "Handles") row.push(s.handles || "");
        else if (colName === "Start TC") row.push(s.start_tc || "");
        else if (colName === "End TC") row.push(s.end_tc || "");
        else if (colName === "Status") row.push(s.status || "Pending");
        else if (colName === "Assign") row.push(s.assign || "");
        else if (colName === "Difficulty") row.push(s.difficulty || "");
        else if (colName === "Prior") row.push(s.prior || "");
        else if (colName === "Notes") row.push(s.notes || "");
        else if (colName === "Timeline") row.push(s.timeline || "");
        else row.push(s[colName] || "");
      }
      rowData.push(row);
    }
    
    var dataRange = sheet.getRange(6, 1, rowData.length, headers.length);
    dataRange.setValues(rowData);
    dataRange.setFontSize(10).setVerticalAlignment("middle");
    
    // Column widths
    var thumbColIdx = headers.indexOf("Thumbnails") + 1;
    if (thumbColIdx > 0) {
      sheet.setColumnWidth(thumbColIdx, 140);
    }

    var statusColIdx = headers.indexOf("Status") + 1;
    for (var r = 0; r < shots.length; r++) {
      var s = shots[r];
      var rowNum = 6 + r;

      // In-cell Thumbnail processing via Google Drive + =IMAGE("url")
      if (s.thumb_b64 && thumbFolder && thumbColIdx > 0) {
        try {
          var decoded = Utilities.base64Decode(s.thumb_b64);
          var blob = Utilities.newBlob(decoded, "image/png", (s.shot_id || "shot") + ".png");
          
          // Overwrite existing file cleanly
          var existingFiles = thumbFolder.getFilesByName((s.shot_id || "shot") + ".png");
          while (existingFiles.hasNext()) {
            existingFiles.next().setTrashed(true);
          }
          var file = thumbFolder.createFile(blob);
          try {
            file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
          } catch (pErr) {}
          
          var imgUrl = "https://drive.google.com/thumbnail?id=" + file.getId() + "&sz=w300";
          sheet.getRange(rowNum, thumbColIdx).setFormula('=IMAGE("' + imgUrl + '")');
          sheet.setRowHeight(rowNum, 54);
          thumbCount++;
        } catch (imgErr) {
          Logger.log("Thumbnail error for " + s.shot_id + ": " + imgErr);
        }
      }

      // Status badge styling
      if (statusColIdx > 0) {
        var st = (s.status || "").toLowerCase();
        var stCell = sheet.getRange(rowNum, statusColIdx);
        if (st.indexOf("appr") !== -1) {
          stCell.setBackground("#d4edda").setFontColor("#155724").setFontWeight("bold");
        } else if (st.indexOf("wip") !== -1) {
          stCell.setBackground("#fff3cd").setFontColor("#856404").setFontWeight("bold");
        } else if (st.indexOf("rev") !== -1) {
          stCell.setBackground("#d1ecf1").setFontColor("#0c5460").setFontWeight("bold");
        } else if (st.indexOf("omit") !== -1) {
          stCell.setBackground("#f8d7da").setFontColor("#721c24").setFontWeight("bold");
        } else {
          stCell.setBackground("#e2e3e5").setFontColor("#383d41");
        }
      }
    }
  }

  return {
    thumbCount: thumbCount,
    driveError: driveErrorMsg
  };
}
