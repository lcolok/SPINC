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
  // includeVerboseError=true gives us the detailed array on current JLCEDA Pro.
  const errors = await eda.pcb_Drc.check(true, true, true);
  if (Array.isArray(errors)) {
    console.log(`[SPINC Rev A] PCB DRC findings: ${errors.length}`, errors);
    return errors.length;
  }
  console.log('[SPINC Rev A] PCB DRC result:', errors);
  return errors === true ? 0 : -1;
}

async function exportRevAAudit() {
  console.log('[SPINC Rev A] Starting JLCEDA audit export...');

  const drcCount = await runPcbDrc();

  // Preserve the exact migrated JLCEDA project as evidence / future SSoT input.
  const projectFile = await eda.sys_FileManager.getProjectFile(
    'SPINC-JLC-Rev-A',
    undefined,
    'epro2',
  );
  await saveRequired(projectFile, 'SPINC-JLC-Rev-A.epro2');

  // Use JLCEDA's native production exporters.  CSV is intentional because the
  // repository-side round-trip verifier consumes text deterministically.
  const bomFile = await eda.pcb_ManufactureData.getBomFile(
    'SPINC-JLC-Rev-A-BOM',
    'csv',
  );
  await saveRequired(bomFile, 'SPINC-JLC-Rev-A-BOM.csv');

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
    drcFindingCount: drcCount,
    exported: [
      'SPINC-JLC-Rev-A.epro2',
      'SPINC-JLC-Rev-A-BOM.csv',
      'SPINC-JLC-Rev-A-CPL.csv',
      'SPINC-JLC-Rev-A-Netlist',
    ],
  };
  console.log('[SPINC Rev A] Audit export complete:', summary);
  eda.sys_Dialog.showInformationMessage(
    `SPINC Rev A audit export complete. PCB DRC findings: ${drcCount}. ` +
      'Now run JLC/verify_jlc_export.py on the exported BOM/CPL before ordering.',
    'SPINC JLC Rev A',
  );
}

await exportRevAAudit();
