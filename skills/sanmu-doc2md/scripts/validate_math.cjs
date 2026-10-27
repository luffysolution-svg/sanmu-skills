#!/usr/bin/env node
/** KaTeX syntax gate. Compilation does not establish source/content accuracy.
 * NODE_PATH may point at an existing node_modules containing katex.
 * node validate_math.cjs formulas.json [--report path] [--allow-empty]
 * Accepts body+display records and legacy $/$$/\\(/\\[ wrapped records.
 */
const fs = require('fs');
const path = require('path');

function loadKatex() {
  const extraPaths = process.env.NODE_PATH ? process.env.NODE_PATH.split(path.delimiter).filter(Boolean) : [];
  for (const candidate of ['katex', path.join(__dirname, 'node_modules', 'katex'),
    path.join(process.cwd(), 'node_modules', 'katex'),
    ...extraPaths.map(p => path.join(p, 'katex'))]) {
    try { return require(candidate); } catch (_) { /* next location */ }
  }
  throw new Error('找不到 katex；请用 NODE_PATH 指向已有的 node_modules。');
}
function escaped(text, index) {
  let count = 0;
  while (index > 0 && text[--index] === '\\') count++;
  return count % 2 === 1;
}
function groupEnd(text, start) {
  let depth = 1;
  for (let i = start + 1; i < text.length; i++) {
    if (escaped(text, i)) continue;
    if (text[i] === '{') depth++;
    if (text[i] === '}' && --depth === 0) return i + 1;
  }
  return null;
}
function structuralIssues(tex) {
  // Match complete control words, skipping balanced text submodes.
  const tags = [];
  for (let i = 0; i < tex.length;) {
    if (tex[i] !== '\\' || escaped(tex, i)) { i++; continue; }
    const match = /^\\([A-Za-z]+)\*?/.exec(tex.slice(i));
    if (!match) { i += 2; continue; }
    let next = i + match[0].length;
    while (/\s/.test(tex[next] || '') && next < tex.length) next++;
    if (/^(text[A-Za-z]*|mbox|hbox|operatorname)$/.test(match[1]) && tex[next] === '{') {
      const end = groupEnd(tex, next);
      if (end !== null) { i = end; continue; }
    }
    if (match[1] === 'tag') {
      tags.push(next);
    }
    i += match[0].length;
  }
  const issues = [];
  if (tags.length > 1) issues.push('Multiple tags; Agent must review source (no numbering guessed)');
  for (const start of tags) {
    const end = tex[start] === '{' ? groupEnd(tex, start) : null;
    if (end === null || !tex.slice(start + 1, end - 1).trim() || /\d\.\s*$/.test(tex.slice(start + 1, end - 1))) {
      issues.push('Truncated or empty tag; Agent must review source');
    }
  }
  let depth = 0;
  for (let i = 0; i < tex.length; i++) {
    if (escaped(tex, i)) continue;
    if (tex[i] === '{') depth++;
    else if (tex[i] === '}' && --depth < 0) break;
  }
  if (depth) issues.push('Unbalanced braces; Agent must review source');
  function skeleton(body, includeText = false) {
    let out = '';
    for (let i = 0; i < body.length;) {
      const group = /^\\(?:text[a-zA-Z]*|mbox|hbox|operatorname)\*?\s*\{/.exec(body.slice(i));
      if (group) {
        const end = groupEnd(body, i + group[0].length - 1);
        if (end !== null) {
          if (includeText) out += skeleton(body.slice(i + group[0].length, end - 1), true);
          i = end; continue;
        }
      }
      const command = /^\\(?:[A-Za-z]+|.)/s.exec(body.slice(i));
      if (command) {
        if (['\\left', '\\right', '\\middle'].includes(command[0])) return '';
        i += command[0].length; continue;
      }
      out += body[i++];
    }
    return out;
  }
  const sk = skeleton(tex), visible = skeleton(tex, true);
  const count = (s, c) => s.split(c).length - 1;
  if ([['(', ')'], ['[', ']']].some(([a, b]) => count(sk, a) !== count(sk, b) && count(visible, a) !== count(visible, b))) {
    issues.push('Unbalanced parentheses/brackets; Agent must review source');
  }
  return issues;
}
function normalizeRecord(record) {
  if (!record || typeof record !== 'object' || typeof record.tex !== 'string') {
    throw new Error('Record must contain a string tex');
  }
  let tex = record.tex.trim();
  let display = record.display;
  if (display !== undefined && typeof display !== 'boolean') throw new Error('display must be boolean');
  // Explicit display means tex is a BODY. Legacy records have no display.
  if (display === undefined) {
    display = false;
    for (const [open, close, mode] of [['$$', '$$', true], ['\\[', '\\]', true], ['\\(', '\\)', false], ['$', '$', false]]) {
      if (tex.startsWith(open)) {
        if (tex.length < open.length + close.length || !tex.endsWith(close)) throw new Error('Unpaired math delimiter; Agent must review source');
        tex = tex.slice(open.length, -close.length).trim();
        display = mode;
        break;
      }
    }
  }
  if (!tex) throw new Error('Empty formula');
  return { tex, display };
}
function main(argv) {
  let target, report, allowEmpty = false;
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--allow-empty') allowEmpty = true;
    else if (argv[i] === '--report' && argv[i + 1]) report = argv[++i];
    else if (!argv[i].startsWith('-') && !target) target = argv[i];
    else throw new Error(`Unknown/incomplete argument: ${argv[i]}`);
  }
  if (!target) throw new Error('用法：node validate_math.cjs formulas.json [--report path] [--allow-empty]');
  const records = JSON.parse(fs.readFileSync(target, 'utf8'));
  if (!Array.isArray(records)) throw new Error('Expected an array of formula records');
  const katex = records.length ? loadKatex() : null;
  const failures = [];
  let passed = 0, formulaCount = 0;
  for (const record of records) {
    if (record && record.kind !== 'parse-error') formulaCount++;
    try {
      if (record && record.kind === 'parse-error') throw new Error((record.errors || ['Math parse error']).join('; '));
      const { tex, display } = normalizeRecord(record);
      const errors = [...(Array.isArray(record.errors) ? record.errors : []), ...structuralIssues(tex)];
      if (errors.length) throw new Error([...new Set(errors)].join('; '));
      katex.renderToString(tex, { displayMode: display, throwOnError: true, strict: false, trust: false });
      passed++;
    } catch (error) {
      failures.push({ file: record?.file, line: record?.line, tex: record?.tex,
        error: `${error.message}; source review required` });
    }
  }
  const emptyRejected = formulaCount === 0 && !allowEmpty;
  const output = {
    total: records.length, formulas: formulaCount, pass: passed,
    failed: failures.length, emptyRejected, failures,
    scope: 'KaTeX syntax/structure only; content accuracy requires comparison with the source',
  };
  const outPath = report || path.join(path.dirname(target), 'math-validation.json');
  fs.writeFileSync(outPath, JSON.stringify(output, null, 2));
  console.log(`公式 ${formulaCount}，编译通过 ${passed}，错误记录 ${failures.length}`);
  for (const item of failures.slice(0, 30)) console.log(`  ${item.file || '?'}:${item.line || '?'} ${item.error}`);
  if (emptyRejected) console.error('未检测到公式；默认校验失败。确认为无公式文档时使用 --allow-empty。');
  console.log('检查仅覆盖语法/结构；内容准确性需 Agent 核对原文。');
  return failures.length || emptyRejected ? 1 : 0;
}
if (require.main === module) {
  try { process.exitCode = main(process.argv.slice(2)); }
  catch (error) { console.error(error.message); process.exitCode = 2; }
}
module.exports = { normalizeRecord, structuralIssues, main };
