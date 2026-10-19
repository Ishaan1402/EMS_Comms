import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '../../..');
const previewDir = path.join(repo, 'outputs/clinician-v2-20261016');
const sourcePath = path.resolve(here, '../pilot_cases.json');
const sourceBytes = await fs.readFile(sourcePath);
const fields = ['case_id', 'encounter_id', 'update_number', 'elapsed_minutes', 'eta_minutes', 'prior_information', 'current_transcript'];
const cases = JSON.parse(sourceBytes).map(row => Object.fromEntries(fields.map((f, i) => [f, row[i]])));
assert.equal(cases.length, 10);
assert.equal(new Set(cases.map(c => c.case_id)).size, 10);
const byId = new Map(cases.map(c => [c.case_id, c]));
const wb = Workbook.create();
const colours = { ink: '#20334A', head: '#203F62', source: '#F0F3F6', input: '#FFF2CC', muted: '#536274', line: '#D4DCE5' };
const sourceCells = [], answerCells = [], validationCells = [], cards = [];

function base(name) {
  const s = wb.worksheets.add(name);
  s.getRange('A1').format.columnWidth = 36;
  s.getRange('B1').format.columnWidth = 112;
  s.showGridLines = false;
  return s;
}
function row(s, r, label, value = '', { height = 30, source = false, input = false, choices = null } = {}) {
  s.getRange(`A${r}:B${r}`).values = [[label, value]];
  s.getRange(`A${r}:B${r}`).format.font = { name: 'Arial', size: 11, color: colours.ink };
  s.getRange(`A${r}:B${r}`).format.verticalAlignment = 'top';
  s.getRange(`A${r}:B${r}`).format.wrapText = true;
  s.getRange(`A${r}:B${r}`).format.rowHeight = height;
  s.getRange(`A${r}`).format.font.bold = true;
  if (source) s.getRange(`B${r}`).format.fill = colours.source;
  if (input) {
    const address = `B${r}`;
    s.getRange(address).format.fill = colours.input;
    answerCells.push({ sheet: s.name, cell: address, label });
    if (choices) {
      s.getRange(address).dataValidation = { rule: { type: 'list', values: choices } };
      validationCells.push({ sheet: s.name, cell: address, choices });
    }
  }
}
function header(s, r, left, right = '') {
  row(s, r, left, right, { height: 30 });
  s.getRange(`A${r}:B${r}`).format.fill = colours.head;
  s.getRange(`A${r}:B${r}`).format.font = { name: 'Arial', size: 11, bold: true, color: '#FFFFFF' };
}
function title(s, text, note) {
  row(s, 2, text, note, { height: 30 });
  s.getRange('A2').format.font = { name: 'Arial', size: 15, bold: true, color: colours.ink };
  s.getRange('A2:B2').format.borders = { bottom: { style: 'thin', color: colours.line } };
}
function reportHeight(text) {
  return Math.max(42, (Math.ceil(text.length / 100) + 1) * 17);
}
function source(s, r, label, value, c, field) {
  row(s, r, label, value, { source: true, height: reportHeight(value) });
  sourceCells.push({ sheet: s.name, cell: `B${r}`, case_id: c.case_id, field, value });
}
function reports(s, r, c) {
  const hasEarlier = c.prior_information !== 'No earlier report provided.';
  row(s, r++, 'Age / sex', `Not supplied / not supplied.${hasEarlier ? '' : ' No earlier report is supplied.'}`, { source: true, height: 28 });
  if (hasEarlier) source(s, r++, `T-${c.elapsed_minutes} min\nEMS Field Report`, c.prior_information, c, 'prior_information');
  source(s, r++, `T=0\n${hasEarlier ? 'Updated EMS Field Report' : 'EMS Field Report'}`, c.current_transcript, c, 'current_transcript');
  return r;
}

const patient = base('Patient review');
patient.tabColor = colours.head;
title(patient, 'EMS patient review v2', 'Fictional reports. Prototype evaluation only. Fill yellow boxes.');
row(patient, 3, 'Reviewer alias', '', { input: true });
row(patient, 4, 'Role / relevant experience', '', { input: true, height: 42 });
row(patient, 5, 'Start with Cases 1–3', 'Discuss whether the questions are understandable before continuing. Use Cannot assess / Unclear when appropriate; do not force a distinction.', { height: 42 });
row(patient, 6, 'Only use supplied facts', 'Do not add ages, sex, doses, test results, or other facts. Put suggested case changes on Form feedback. Earlier reports appear first; T=0 is the latest report, not a real clock time.', { height: 58 });
row(patient, 7, 'Attention options', 'Immediate review = review now before arrival. Review soon = prompt review at next opportunity. Routine awareness = keep visible, no extra immediate review indicated. Cannot assess = insufficient information or outside expertise.', { height: 58 });
row(patient, 8, 'Not formal triage', 'These draft labels concern reviewing reports before arrival, not treatment order, bed allocation, ESI, or validated triage. Meaningful change may be improvement, not deterioration.', { height: 42 });
row(patient, 9, 'Missing information', 'Unknown is not normal or absent. Briefly explain gaps or scope limits. There is no required handoff length. See the separate short guide for definitions.', { height: 42 });
patient.freezePanes.freezeRows(2);
let r = 11;
for (const [index, c] of cases.entries()) {
  const first = r;
  header(patient, r++, `Case ${index + 1} / ${c.case_id}`, `ETA ${c.eta_minutes} minutes at latest report`);
  r = reports(patient, r, c);
  row(patient, r++, 'EMS to ED Handoff', '', { input: true, height: 76 });
  row(patient, r++, 'Meaningful change?', '', { input: true, choices: ['Yes', 'No', 'Unclear', 'No earlier report'] });
  row(patient, r++, 'Direction (if Yes)', '', { input: true, choices: ['Improved', 'Worsened', 'Mixed', 'Other', 'Unclear'] });
  row(patient, r++, 'Evidence / missing information', '', { input: true, height: 68 });
  row(patient, r++, 'Attention before arrival', '', { input: true, choices: ['Immediate review', 'Review soon', 'Routine awareness', 'Cannot assess'] });
  row(patient, r++, 'Reason for attention / limits', '', { input: true, height: 60 });
  cards.push({ case_id: c.case_id, range: `A${first}:B${r - 1}` });
  patient.getRange(`A${r}:B${r}`).format.rowHeight = 16;
  r++;
}

const attention = base('Attention exercise');
attention.tabColor = '#57738F';
title(attention, 'Attention exercise', 'Optional. Two separate groups. No treatment-order or bed-assignment decision.');
row(attention, 3, 'Question', 'Who should the receiving team review first for planning before arrival? All patients may receive care concurrently. You may say ranking is not meaningful.', { height: 42 });
row(attention, 4, 'How to answer', 'Use 1, 2, or 3. Lowest number = first to review. Equal numbers = an intentional tie. Use Cannot assess if the order is not justified. Explain each position or tie.', { height: 42 });
row(attention, 5, 'What is unknown', 'Existing ED demand, staff availability, beds, and activation status are not supplied. Do not assume a preparation team or empty bays. State any missing context that prevents an answer.', { height: 58 });
row(attention, 6, 'Timing', 'Each group is an independent hypothetical snapshot. T=0 labels each patient\'s latest report; earlier reports are shown first. ETAs are the remaining times stated in those reports.', { height: 42 });
attention.freezePanes.freezeRows(2);
r = 8;
const groupRanges = [];
for (const [groupIndex, ids] of [['SYN001', 'SYN004', 'SYN007'], ['SYN002', 'SYN008', 'SYN010']].entries()) {
  const first = r;
  header(attention, r++, `Group ${groupIndex + 1} / A0${groupIndex + 1}`, 'Separate receiving-ED scenario. Only supplied reports and ETAs are known.');
  row(attention, r++, 'Is ranking meaningful here?', '', { input: true, choices: ['Yes', 'Only some comparisons', 'No', 'Cannot assess'] });
  row(attention, r++, 'Why / missing context?', '', { input: true, height: 60 });
  for (const id of ids) {
    const c = byId.get(id);
    header(attention, r++, id, `ETA ${c.eta_minutes} minutes at latest report`);
    r = reports(attention, r, c);
    row(attention, r++, 'Attention order', '', { input: true, choices: ['1', '2', '3', 'Cannot assess'] });
    row(attention, r++, 'Reason for position / tie / limits', '', { input: true, height: 68 });
  }
  groupRanges.push(`A${first}:B${r - 1}`);
  attention.getRange(`A${r}:B${r}`).format.rowHeight = 18;
  r++;
}

const feedback = base('Form feedback');
feedback.tabColor = '#8C98A5';
title(feedback, 'Form feedback', 'Optional. Keep proposed changes separate from patient answers.');
row(feedback, 3, 'Purpose', 'Tell us if the language, cases, or requested judgments do not match your experience. Do not rewrite the source reports or introduce hypothetical facts into handoff answers.', { height: 58 });
row(feedback, 5, 'Are the questions clear?', '', { input: true, choices: ['Yes', 'Partly', 'No'] });
row(feedback, 6, 'Are these judgments within your experience?', '', { input: true, choices: ['Yes', 'Partly', 'No'], height: 42 });
row(feedback, 7, 'What should we change?', '', { input: true, height: 110 });
row(feedback, 8, 'Case IDs / missing facts that matter', '', { input: true, height: 110 });
row(feedback, 9, 'Any concern with the attention labels or ranking task?', '', { input: true, height: 110 });
row(feedback, 10, 'Other workflow feedback', '', { input: true, height: 110 });
row(feedback, 12, 'Source / versions', 'Cases: pilot_v1 (unchanged, fictional AI-authored text; no source audio). Form: clinician_form_v2. Draft rubric: review_attention_v2_draft. Exercises: attention_v2. Not clinical validation.', { height: 58 });

for (const item of answerCells) {
  const value = wb.worksheets.getItem(item.sheet).getRange(item.cell).values[0][0];
  assert.ok(value === '' || value === null, `Nonblank answer: ${item.sheet}!${item.cell}`);
}
for (const item of sourceCells) {
  assert.equal(wb.worksheets.getItem(item.sheet).getRange(item.cell).values[0][0], byId.get(item.case_id)[item.field]);
}
assert.equal(validationCells.length, 40);
wb.recalculate();
await fs.mkdir(previewDir, { recursive: true });
console.log((await wb.inspect({ kind: 'sheet', include: 'id,name', maxChars: 1500 })).ndjson);
console.log((await wb.inspect({ kind: 'region', sheetId: 'Patient review', range: cards[0].range, maxChars: 3500, tableMaxRows: 15, tableMaxCols: 2 })).ndjson);
console.log((await wb.inspect({ kind: 'match', searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#NULL!', options: { useRegex: true, maxResults: 30 }, maxChars: 1500 })).ndjson);
const previews = [
  ['patient-intro', 'Patient review', 'A1:B9'],
  ['patient-first', 'Patient review', cards[0].range],
  ['patient-updated', 'Patient review', cards[1].range],
  ['patient-last', 'Patient review', cards.at(-1).range],
  ['attention-intro', 'Attention exercise', 'A1:B6'],
  ['attention-first', 'Attention exercise', groupRanges[0]],
  ['attention-second', 'Attention exercise', groupRanges[1]],
  ['feedback', 'Form feedback', 'A1:B12'],
];
for (const [name, sheetName, range] of previews) {
  const blob = await wb.render({ sheetName, range, scale: 1.4, format: 'png' });
  await fs.writeFile(path.join(previewDir, `${name}.png`), new Uint8Array(await blob.arrayBuffer()));
}
const result = await SpreadsheetFile.exportXlsx(wb);
const temporaryXlsx = path.join(previewDir, 'Annotation_Notebook_v2.xlsx');
await result.save(temporaryXlsx);
await fs.rename(temporaryXlsx, path.join(here, 'Annotation_Notebook_v2.xlsx'));
await fs.writeFile(path.join(previewDir, 'source-input-manifest.json'), JSON.stringify({
  kind: 'blank_form_source_manifest', form_version: 'clinician_form_v2', dataset_version: 'pilot_v1',
  rubric_version: 'review_attention_v2_draft', attention_exercise_version: 'attention_v2',
  source_sha256: createHash('sha256').update(sourceBytes).digest('hex'),
  sourceCells, answerCells, validationCells, cards,
  groups: [{ exercise_id: 'A01', case_ids: ['SYN001', 'SYN004', 'SYN007'] }, { exercise_id: 'A02', case_ids: ['SYN002', 'SYN008', 'SYN010'] }]
}, null, 2));
console.log(JSON.stringify({ workbook: path.join(here, 'Annotation_Notebook_v2.xlsx'), cases: cases.length, sourceCells: sourceCells.length, blankInputs: answerCells.length, dropdowns: validationCells.length, previews: previews.length }));
