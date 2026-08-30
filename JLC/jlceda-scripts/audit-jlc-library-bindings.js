/*
 * SPINC JLC Rev A - read-only JLC system-library binding audit
 *
 * Run from JLCEDA Pro V3: Advanced -> Run Script, after the KiCad Rev A
 * migration is open. Select JLC/jlc-bindings.json when prompted.
 *
 * SAFETY / SCOPE:
 * - READ ONLY: this script never creates, replaces, modifies or deletes a
 *   schematic/PCB component or a library item.
 * - It performs exact system-library queries by frozen LCSC C-number and
 *   records the associated JLC symbol/footprint UUIDs/names.
 * - A single exact C-number hit is only a sourcing/library identity match; it
 *   is NOT permission to replace the imported KiCad footprint automatically.
 * - The output is evidence for a later human/automated package review.
 */

const MANIFEST_INTENT =
  'Golden JLC Rev A populated-device binding manifest generated from the frozen KiCad production BOM.';

async function selectManifest() {
  const file = await eda.sys_FileSystem.openReadFileDialog(['json'], false);
  if (!file) {
    throw new Error('No binding manifest selected');
  }
  const manifest = JSON.parse(await file.text());
  if (manifest.schema !== 1 || manifest.intent !== MANIFEST_INTENT) {
    throw new Error('Selected JSON is not the SPINC JLC Rev A binding manifest');
  }
  if (!Array.isArray(manifest.bindings) || manifest.bindings.length !== manifest.uniqueLcscCount) {
    throw new Error('Binding manifest structure/count is inconsistent');
  }
  if (!Array.isArray(manifest.dnpReferences) || !manifest.dnpReferences.includes('TH3')) {
    throw new Error('Binding manifest lost the frozen TH3 DNP invariant');
  }
  return { file, manifest };
}

function resultSupplierId(item, fallback) {
  return item?.otherProperty?.supplierId || item?.supplierId || fallback;
}

function summarizeDevice(item, lcsc) {
  return {
    lcsc: resultSupplierId(item, lcsc),
    name: item?.name || null,
    deviceUuid: item?.uuid || null,
    libraryUuid: item?.libraryUuid || null,
    symbol: item?.symbol
      ? {
          name: item.symbol.name || null,
          uuid: item.symbol.uuid || null,
          libraryUuid: item.symbol.libraryUuid || null,
        }
      : {
          name: item?.symbolName || null,
          uuid: item?.symbolUuid || null,
          libraryUuid: null,
        },
    footprint: item?.footprint
      ? {
          name: item.footprint.name || null,
          uuid: item.footprint.uuid || null,
          libraryUuid: item.footprint.libraryUuid || null,
        }
      : {
          name: item?.footprintName || null,
          uuid: item?.footprintUuid || null,
          libraryUuid: null,
        },
    model3D: item?.model3D
      ? {
          name: item.model3D.name || null,
          uuid: item.model3D.uuid || null,
          libraryUuid: item.model3D.libraryUuid || null,
        }
      : null,
  };
}

async function currentPcbFootprints() {
  try {
    const components = await eda.pcb_PrimitiveComponent.getAll();
    const byReference = {};
    for (const component of components) {
      const reference = component.getState_Designator();
      if (!reference) continue;
      byReference[reference] = {
        footprint: component.getState_Footprint(),
        layer: component.getState_Layer?.() ?? null,
        x: component.getState_X?.() ?? null,
        y: component.getState_Y?.() ?? null,
        rotation: component.getState_Rotation?.() ?? null,
      };
    }
    return { available: true, componentCount: components.length, byReference };
  } catch (error) {
    console.warn('[SPINC Rev A] Could not inspect current PCB components:', error);
    return {
      available: false,
      componentCount: null,
      byReference: {},
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

async function exactSystemMatches(lcsc) {
  // searchByProperties is deliberately used one C-number at a time so we can
  // classify zero/one/multiple results without relying on batch-result ordering.
  const matches = await eda.lib_Device.searchByProperties(
    { supplierId: lcsc },
    undefined,
    undefined,
    undefined,
    100,
    1,
  );
  return Array.isArray(matches) ? matches : [];
}

async function runAudit() {
  const { file, manifest } = await selectManifest();
  const systemLibraryUuid = await eda.lib_LibrariesList.getSystemLibraryUuid();
  const pcb = await currentPcbFootprints();

  const rows = [];
  let unique = 0;
  let missing = 0;
  let ambiguous = 0;

  console.log(
    `[SPINC Rev A] Auditing ${manifest.uniqueLcscCount} unique C-numbers / ` +
      `${manifest.componentCount} populated designators against JLC system library ${systemLibraryUuid}`,
  );

  for (const binding of manifest.bindings) {
    const lcsc = binding.lcsc;
    const matches = await exactSystemMatches(lcsc);
    let status = 'missing';
    if (matches.length === 1) {
      status = 'unique';
      unique += 1;
    } else if (matches.length > 1) {
      status = 'ambiguous';
      ambiguous += 1;
    } else {
      missing += 1;
    }

    const imported = {};
    for (const reference of binding.references) {
      imported[reference] = pcb.byReference[reference] || null;
    }

    const row = {
      lcsc,
      status,
      matchCount: matches.length,
      references: binding.references,
      sourceValues: binding.sourceValues,
      sourceFootprints: binding.sourceFootprints,
      importedPcb: imported,
      systemMatches: matches.map((item) => summarizeDevice(item, lcsc)),
    };
    rows.push(row);
    console.log(`[SPINC Rev A] ${lcsc}: ${status} (${matches.length})`, row);
  }

  const report = {
    schema: 1,
    name: 'SPINC JLC Rev A system-library binding audit',
    readOnly: true,
    apiStatus: 'JLCEDA Pro library APIs are BETA; runtime output requires review',
    manifestFile: file.name,
    manifestComponentCount: manifest.componentCount,
    manifestUniqueLcscCount: manifest.uniqueLcscCount,
    dnpReferences: manifest.dnpReferences,
    systemLibraryUuid,
    currentPcb: {
      available: pcb.available,
      componentCount: pcb.componentCount,
      error: pcb.error || null,
    },
    summary: {
      unique,
      missing,
      ambiguous,
      passedIdentityLookup: missing === 0 && ambiguous === 0,
      automaticReplacementAuthorized: false,
    },
    bindings: rows,
  };

  const text = JSON.stringify(report, null, 2) + '\n';
  await eda.sys_FileSystem.saveFile(
    new Blob([text], { type: 'application/json' }),
    'SPINC-JLC-Rev-A-Library-Audit.json',
  );

  console.log('[SPINC Rev A] JLC library audit complete:', report.summary);
  const headline =
    `JLC library audit: ${unique} unique / ${missing} missing / ${ambiguous} ambiguous. ` +
    'No components were modified.';

  if (missing === 0 && ambiguous === 0) {
    await eda.sys_Dialog.showInformationMessage(
      headline + ' Exact C-number identity lookup passed; footprint/package compatibility still requires review.',
      'SPINC JLC Rev A',
    );
  } else {
    await eda.sys_Dialog.showWarningMessage(
      headline + ' Resolve library lookup exceptions before any binding work.',
      'SPINC JLC Rev A',
    );
  }
  return report;
}

(async () => {
  try {
    await runAudit();
  } catch (error) {
    console.error('[SPINC Rev A] Library binding audit FAILED:', error);
    const message = error instanceof Error ? error.message : String(error);
    try {
      await eda.sys_Dialog.showErrorMessage(
        `SPINC Rev A library binding audit failed: ${message}`,
        'SPINC JLC Rev A',
      );
    } catch (dialogError) {
      console.error('[SPINC Rev A] Could not show failure dialog:', dialogError);
    }
    throw error;
  }
})();
