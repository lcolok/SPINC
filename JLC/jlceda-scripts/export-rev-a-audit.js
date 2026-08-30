/*
 * SPINC JLC Rev A - JLCEDA Pro post-migration audit exporter
 *
 * Run from JLCEDA Pro V3: Advanced -> Run Script, with the migrated PCB open.
 * The APIs used here are currently BETA in JLCEDA Pro; this script is an
 * evidence/export helper, not a substitute for human DFM review.
 *
 * Expected outputs (through JLCEDA's save dialogs):
 *   - SPINC-JLC-Rev-A.epro2
 *   - SPINC-JLC-Rev-A-BOM.csv
 *   - SPINC-JLC-Rev-A-CPL.csv
 *   - SPINC-JLC-Rev-A-Netlist.*
 *
 * The BOM export is intentionally configured instead of relying on the user's
 * current/default BOM template.  In particular, JLCEDA's "Supplier Part"
 * field is exported under the stable title "LCSC Part #" so the repository
 * round-trip gate can compare every designator to the frozen upstream C-number.
 *
 * After exporting, verify BOM/CPL in the repository with:
 *   python JLC/verify_jlc_export.py \
 *     --bom SPINC-JLC-Rev-A-BOM.csv \
 *     --cpl SPINC-JLC-Rev-A-CPL.csv
 */

async function saveRequired(file, name) {
  if (!file) {
    throw new Error(`${name}: JLCEDA returned no file`);
  }
  await eda.sys_FileSystem.saveFile(file, name);
}

async function runPcbDrc() {
  // includeVerboseError=true asks current JLCEDA Pro for the detailed finding
  // array.  We keep exporting even when findings exist so the evidence bundle
  // can be inspected; a non-zero DRC count is never a manufacturing approval.
  const result = await eda.pcb_Drc.check(true, true, true);
  if (Array.isArray(result)) {
    console.log(`[SPINC Rev A] PCB DRC findings: ${result.length}`, result);
    return { count: result.length, result };
  }
  console.log('[SPINC Rev A] PCB DRC result:', result);
  return { count: result === true ? 0 : -1, result };
}

async function exportRevAAudit() {
  console.log('[SPINC Rev A] Starting JLCEDA audit export...');

  const drc = await runPcbDrc();

  // Preserve the exact migrated JLCEDA project as evidence / future SSoT input.
  const projectFile = await eda.sys_FileManager.getProjectFile(
    'SPINC-JLC-Rev-A',
    undefined,
    'epro2',
  );
  await saveRequired(projectFile, 'SPINC-JLC-Rev-A.epro2');

  // Do not depend on a user's cloud-synced/default BOM configuration.  Force
  // the identity-bearing fields that matter to Rev A and title Supplier Part
  // as "LCSC Part #".  After JLC library rebinding, this must contain C-numbers.
  const bomFile = await eda.pcb_ManufactureData.getBomFile(
    'SPINC-JLC-Rev-A-BOM',
    'csv',
    undefined,
    [{ property: 'Add into BOM', includeValue: 'yes' }],
    ['Quantity'],
    ['Designator', 'Value', 'Footprint', 'Supplier', 'Supplier Part'],
    [
      { property: 'Designator', title: 'Designator', sort: 'asc', group: 'No', orderWeight: 100 },
      { property: 'Quantity', title: 'Quantity', group: 'Yes', orderWeight: 90 },
      { property: 'Value', title: 'Value', group: 'Yes', orderWeight: 80 },
      { property: 'Footprint', title: 'Footprint', group: 'Yes', orderWeight: 70 },
      { property: 'Supplier', title: 'Supplier', group: 'Yes', orderWeight: 60 },
      { property: 'Supplier Part', title: 'LCSC Part #', group: 'Yes', orderWeight: 50 },
    ],
  );
  await saveRequired(bomFile, 'SPINC-JLC-Rev-A-BOM.csv');

  // JLCPCB expects placement coordinates in mm.  The repository verifier can
  // tolerate one global origin shift, but no per-component relative movement,
  // layer change or rotation drift.
  const cplFile = await eda.pcb_ManufactureData.getPickAndPlaceFile(
    'SPINC-JLC-Rev-A-CPL',
    'csv',
    ESYS_Unit.MILLIMETER,
  );
  await saveRequired(cplFile, 'SPINC-JLC-Rev-A-CPL.csv');

  // Netlist gives us a second electrical representation independent from the
  // BOM/CPL round trip and is useful for pin/net equivalence archaeology.
  const netlistFile = await eda.pcb_ManufactureData.getNetlistFile(
    'SPINC-JLC-Rev-A-Netlist',
    ESYS_NetlistType.JLCEDA_PRO,
  );
  await saveRequired(netlistFile, 'SPINC-JLC-Rev-A-Netlist');

  const summary = {
    project: 'SPINC-JLC-Rev-A',
    drcFindingCount: drc.count,
    exported: [
      'SPINC-JLC-Rev-A.epro2',
      'SPINC-JLC-Rev-A-BOM.csv',
      'SPINC-JLC-Rev-A-CPL.csv',
      'SPINC-JLC-Rev-A-Netlist',
    ],
  };
  console.log('[SPINC Rev A] Audit export complete:', summary);
  await eda.sys_Dialog.showInformationMessage(
    `SPINC Rev A audit export complete. PCB DRC findings: ${drc.count}. ` +
      'Now run JLC/verify_jlc_export.py on the exported BOM/CPL before ordering.',
    'SPINC JLC Rev A',
  );
}

// Use an async IIFE instead of top-level await so the same file can be parsed by
// ordinary JavaScript tooling and remains compatible with conservative script
// runtimes.  Runtime API errors are surfaced both in the console and a dialog.
(async () => {
  try {
    await exportRevAAudit();
  } catch (error) {
    console.error('[SPINC Rev A] Audit export FAILED:', error);
    const message = error instanceof Error ? error.message : String(error);
    try {
      await eda.sys_Dialog.showErrorMessage(
        `SPINC Rev A audit export failed: ${message}`,
        'SPINC JLC Rev A',
      );
    } catch (dialogError) {
      console.error('[SPINC Rev A] Could not show failure dialog:', dialogError);
    }
    throw error;
  }
})();
