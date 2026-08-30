/*
 * SPINC JLC Rev A - guarded KiCad migration helper for JLCEDA Pro V3
 *
 * Run from: Advanced -> Run Script
 * Select ONLY the CI-produced SPINC-JLC-Rev-A-KiCad-*.zip artifact.
 *
 * This helper intentionally imports documents only.  It does NOT extract the
 * KiCad libraries into the user's JLCEDA library, and it does NOT declare the
 * result production-ready.  After import, re-bind populated devices to their
 * frozen JLC/LCSC identities and run export-rev-a-audit.js + repository gates.
 */

const EXPECTED_NAME = /SPINC-JLC-Rev-A-KiCad.*\.zip$/i;

async function selectMigrationBundle() {
  const file = await eda.sys_FileSystem.openReadFileDialog(['zip'], false);
  if (!file) {
    throw new Error('No migration ZIP selected');
  }
  if (!EXPECTED_NAME.test(file.name)) {
    throw new Error(
      `Refusing unexpected ZIP: ${file.name}. Select the CI-produced SPINC-JLC-Rev-A-KiCad ZIP.`,
    );
  }
  if (file.size < 100000) {
    throw new Error(`Migration ZIP is unexpectedly small (${file.size} bytes)`);
  }
  return file;
}

async function importRevA() {
  const file = await selectMigrationBundle();
  console.log('[SPINC Rev A] Selected migration bundle:', {
    name: file.name,
    size: file.size,
    type: file.type,
  });

  // Best-effort introspection only.  Not every importer exposes useful project
  // metadata here, so a failure to extract info is logged but does not replace
  // the actual importer's validation.
  try {
    const info = await eda.sys_FileManager.extractProjectInfo(file);
    console.log('[SPINC Rev A] Source project info:', info);
  } catch (error) {
    console.warn('[SPINC Rev A] Could not pre-extract project info:', error);
  }

  // Literal enum values are used deliberately so the standalone script does
  // not depend on enum globals being exposed by a particular JLCEDA build.
  const imported = await eda.sys_FileManager.importProjectByProjectFile(
    file,
    'KiCad',
    {
      importOption: 'ImportDocument',
      schematicObjectStyle: 'custom',
      associateFootprint: true,
      associate3DModel: true,
      importFootprintNotesLayer: true,
    },
  );

  if (!imported) {
    throw new Error('JLCEDA returned no imported project');
  }

  console.log('[SPINC Rev A] KiCad import returned:', imported);
  await eda.sys_Dialog.showInformationMessage(
    'SPINC Rev A KiCad import completed. This is only a migration result, not a production approval. ' +
      'Next: inspect/rebind JLC devices, then run export-rev-a-audit.js and the repository round-trip gates.',
    'SPINC JLC Rev A',
  );
  return imported;
}

(async () => {
  try {
    await importRevA();
  } catch (error) {
    console.error('[SPINC Rev A] KiCad import FAILED:', error);
    const message = error instanceof Error ? error.message : String(error);
    try {
      await eda.sys_Dialog.showErrorMessage(
        `SPINC Rev A KiCad import failed: ${message}`,
        'SPINC JLC Rev A',
      );
    } catch (dialogError) {
      console.error('[SPINC Rev A] Could not show failure dialog:', dialogError);
    }
    throw error;
  }
})();
