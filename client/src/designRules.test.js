import fs from 'fs';
import path from 'path';

// The look of the app is a small set of rules (see ui.js). This test reads the source and fails when
// a rule is broken, so the screens cannot drift back into mixed fonts, native accordions and emoji.
// KNOWN lists what is still to be cleaned up, file by file; it can only shrink: a rule that is no
// longer broken must be taken off the list, so a finished file stays finished.

const RULES = {
  'native <details> accordion': /<(details|summary)\b/,
  'monospace type': /\bfont-mono\b/,
  'all-caps label': /\buppercase\b|\btracking-/,
  'type size off the 12/14/16/20 scale': /\btext-(lg|2xl|3xl|4xl)\b|\btext-\[/,
  'heavy font weight': /\bfont-(bold|extrabold|black)\b/,
  'emoji or glyph used as an icon': /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}▲▼▶◀]/u,
  // The one allowed use of amber as a colour block is the 6px status dot (bg-amber-500).
  'amber, yellow or orange background or border': /\b(bg|border|ring)-(amber|yellow|orange)-(?!500\b)/,
};

// Still to do: the Settings page (step C) and the case pane (step D). LiveCaseRecorder.js is the EMT
// screen, which waits for Astra's recorder branch.
const KNOWN = {
  'components/CaseOverview.js': ['native <details> accordion', 'monospace type', 'all-caps label',
    'amber, yellow or orange background or border'],
  'components/CaseUpdateForm.js': ['native <details> accordion'],
  'components/HospitalWorkflowSettings.js': ['native <details> accordion'],
  'components/LiveCaseRecorder.js': ['emoji or glyph used as an icon'],
  'components/LiveTranscript.js': ['monospace type'],
};

const read = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
  const full = path.join(dir, entry.name);
  if (entry.isDirectory()) return entry.name === 'node_modules' ? [] : read(full);
  return /\.js$/.test(entry.name) && !/\.test\.js$/.test(entry.name) ? [full] : [];
});

// Comments may name a rule they are about; only code counts.
const code = (source) => source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|\s)\/\/.*$/gm, '$1');

const found = {};
read(__dirname).forEach((file) => {
  const source = code(fs.readFileSync(file, 'utf8'));
  Object.entries(RULES).forEach(([rule, pattern]) => {
    if (pattern.test(source)) (found[path.relative(__dirname, file)] ||= []).push(rule);
  });
});

test('no file breaks a design rule it is not already known to break', () => {
  const unexpected = Object.entries(found).flatMap(([file, rules]) => rules
    .filter((rule) => !(KNOWN[file] || []).includes(rule)).map((rule) => `${file}: ${rule}`));
  expect(unexpected).toEqual([]);
});

test('a rule that is fixed comes off the list, so a finished file stays finished', () => {
  const stale = Object.entries(KNOWN).flatMap(([file, rules]) => rules
    .filter((rule) => !(found[file] || []).includes(rule)).map((rule) => `${file}: ${rule}`));
  expect(stale).toEqual([]);
});

test('every rule can actually fire (the patterns are not silently dead)', () => {
  const samples = {
    'native <details> accordion': '<details>', 'monospace type': 'font-mono', 'all-caps label': 'uppercase',
    'type size off the 12/14/16/20 scale': 'text-3xl', 'heavy font weight': 'font-bold',
    'emoji or glyph used as an icon': '🏥 ⚙ ▲', 'amber, yellow or orange background or border': 'bg-amber-50',
  };
  Object.entries(RULES).forEach(([rule, pattern]) => expect(pattern.test(samples[rule])).toBe(true));
  expect(RULES['amber, yellow or orange background or border'].test('bg-amber-500')).toBe(false);   // the dot
});
