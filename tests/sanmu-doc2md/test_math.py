"""Regression tests for conservative normalization and the actual KaTeX gate."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = (Path(__file__).resolve().parents[2] / 'skills' / 'sanmu-doc2md') / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from normalize_math import (normalize_math, process, scan_math, math_issues,
                            fix_inline_parens, protected_markdown, plain_text,
                            normalize_heading, normalize_prose_brackets)
from extract_formulas import extract


class MathTests(unittest.TestCase):
    def test_complete_legal_macros_untouched(self):
        source = r'\neq \ne \subseteq \subsetneq \infty \int \iint \intop \intercal \times \rightarrow \longrightarrow'
        self.assertEqual(normalize_math(source), source)

    def test_only_complete_whitelisted_words(self):
        self.assertEqual(normalize_math(r'\vertHg+\cdotkg'), r'\vert Hg+\cdot kg')
        source = r'\vertHgx+\cdotkgmore+\cdotm+\intensity'
        self.assertEqual(normalize_math(source), source)

    def test_tags_and_arrows_are_never_guessed(self):
        for source in [r'a\tag{7}+b\tag{12.26}c', r'\tag{13.}\tag{13.49}',
                       r'\xrightarrow[x]y}', r'\tag{12.45) $ ^{①} \}',
                       r'\tag*{(12.45)\(^{\text{①}}\)}']:
            with self.subTest(source=source):
                self.assertEqual(normalize_math(source), source)
        self.assertTrue(math_issues(r'a\tag{7}+b\tag{12.26}c'))
        self.assertTrue(math_issues(r'a\tag{13.}'))
        self.assertTrue(math_issues(r'a\tag{13'))

    def test_font_aliases_and_nested_scope(self):
        self.assertEqual(normalize_math(r'\it{x}+\rm{y}'), r'\mathit{x}+\mathrm{y}')
        self.assertEqual(normalize_math(r'{\it x_{a}}+{\rm kg}'), r'{\mathit{x_{a}}}+{\mathrm{kg}}')
        self.assertEqual(normalize_math(r'\it x + \rm y'), r'\it x + \rm y')

    def test_text_modes_remain_opaque(self):
        for source in [r'\text{\vertHg $a$ \(b\) （中文）}',
                       r'\text{nested \text{A} $a$}+\mathrm{x}',
                       r'\operatorname{foo(}+x', r'\text{NH_4Cl/石墨}']:
            self.assertEqual(normalize_math(source), source)
        self.assertEqual(math_issues(r'\text{literal ( [ $a$} + \left. x \right)'), [])

    def test_inequality_normalization_for_markdown_safety(self):
        self.assertEqual(normalize_math(r'x < y'), r'x \lt y')
        self.assertEqual(normalize_math(r'x > y'), r'x \gt y')
        self.assertEqual(normalize_math(r'0<x<1'), r'0\lt x\lt 1')
        self.assertEqual(normalize_math(r'\Omega > 0'), r'\Omega \gt 0')
        self.assertEqual(normalize_math(r'\text{a < b} + x < y'), r'\text{a < b} + x \lt y')

    def test_visible_brackets_can_span_latex_text_groups(self):
        for body in [r'[\text{Au(NO}_{3})_{3}]',
                     r'\text{KOH(质量分数} w=0.22)', r'\text{literal (}']:
            self.assertEqual(math_issues(body), [])
        source = r'正文（$\text{KOH(质量分数}w=0.22)$'
        self.assertEqual(process(source), source)

    def test_markdown_protection_and_escape_parity(self):
        source = ('`$inline$` ``a `$nested$` b``\n~~~tex\n$code$\n~~~\n'
                  '    $indented$\n![price $alt$](image.png)\n'
                  '<code>$html$</code><!-- $comment$ -->\n'
                  r'\$escaped \$ and \\$x$ and $y$')
        spans, errors = scan_math(source)
        self.assertEqual([s.body for s in spans], ['x', 'y'])
        self.assertEqual(errors, [])
        self.assertEqual(process(source), source)
        masked = protected_markdown(source)
        self.assertEqual(len(masked), len(source))
        self.assertNotIn('$code$', masked)
        self.assertNotIn('$alt$', masked)

    def test_adjacent_inline_formulas_do_not_open_display(self):
        spans, errors = scan_math(r'$z_{+}\\nu_{+} =$$|z_{-}|\\nu_{-}$ 的关系')
        self.assertEqual(errors, [])
        self.assertEqual([s.body for s in spans], [r'z_{+}\\nu_{+} =', r'|z_{-}|\\nu_{-}'])
        self.assertEqual([s.display for s in spans], [False, False])

    def test_all_outer_delimiters_and_nested_text_math(self):
        source = r'$x+\text{a $b$ \(c\)}$ $$\tag*{(12.45)\(^{\text{①}}\)}x$$ \(z\) \[w\]'
        spans, errors = scan_math(source)
        self.assertEqual(errors, [])
        self.assertEqual(len(spans), 4)
        self.assertEqual([s.display for s in spans], [False, True, False, True])
        self.assertIn('$b$', spans[0].body)
        self.assertEqual(process(source), source)

    def test_indentation_inside_display_is_not_code(self):
        source = '$$\n    x + y\n$$\n\n    $code$\n'
        spans, errors = scan_math(source)
        self.assertEqual(len(spans), 1)
        self.assertTrue(spans[0].display)
        self.assertEqual(errors, [])

    def test_unpaired_dollars_reported_without_pairing_across_code(self):
        for source in ['$x\nrest', '$$x', '$x `code` y$', '$x\n```\n$y$\n```',
                       '$$\n    x\n\n```\n$$\n```']:
            with self.subTest(source=source):
                self.assertTrue(scan_math(source)[1])

    def test_process_repairs_boundary_and_preserves_tail_order(self):
        self.assertEqual(process('正文（$x)$。'), '正文（$x$）。')
        self.assertEqual(process(r'直链 $(\mathrm{H}_2$ 和 $Cl_2$ 反应）'),
                         r'直链 ($\mathrm{H}_2$ 和 $Cl_2$ 反应）')
        self.assertEqual(process(r'范围在 $(1800\mathrm{cm}^{-1}$ 和 $1928\mathrm{cm}^{-1})$ 之间'),
                         r'范围在 ($1800\mathrm{cm}^{-1}$ 和 $1928\mathrm{cm}^{-1}$) 之间')
        source = r'$\text{和像差（主要是球差} x^3) 。$'
        self.assertEqual(process(source), '和像差（主要是球差$x^3$）。')
        self.assertEqual(process(process(source)), process(source))

    def test_no_unproven_boundary_repair(self):
        for source in [r'$f(x$ unknown', r'$x]$', r'$\text{(}$',
                       r'`正文（$x)$`', r'正文（$\left.x\right)$',
                       r'正文（$x)+y$']:
            self.assertEqual(fix_inline_parens(source), source)

    def test_plain_symbols_match_complete_control_words(self):
        self.assertEqual(plain_text(r'\subseteq \infty \neq'), 'subseteq ∞ neq')

    def test_plain_text_subscripts_and_superscripts(self):
        self.assertEqual(plain_text(r'CO_2'), 'CO₂')
        self.assertEqual(plain_text(r'\mathrm{CO}_{2}'), 'CO₂')
        self.assertEqual(plain_text(r'Al_2O_3@Fe_3O_4'), 'Al₂O₃@Fe₃O₄')
        self.assertEqual(plain_text(r'H_2-TPR'), 'H₂-TPR')
        self.assertEqual(plain_text(r'N_2'), 'N₂')
        self.assertEqual(plain_text(r'x^2 + y^3'), 'x² + y³')

    def test_normalize_heading_preserves_math_and_subscripts(self):
        self.assertEqual(normalize_heading(r'1.3.2 $\mathrm{CO}_2$ 辅助乙烷脱氢'), r'1.3.2 $\mathrm{CO}_2$ 辅助乙烷脱氢')
        self.assertEqual(normalize_heading(r'\textbf{1.3.2} $\mathrm{CO}_2$ 辅助乙烷脱氢'), r'1.3.2 $\mathrm{CO}_2$ 辅助乙烷脱氢')
        self.assertEqual(normalize_heading(r'### 1.3.2 CO_2 辅助乙烷脱氢'), r'### 1.3.2 CO₂ 辅助乙烷脱氢')
        self.assertEqual(normalize_heading(r'3.4.5 程序升温脱附（ $\mathrm{NH}_3$ -TPD， $\mathrm{CO}_2$ -TPD）'),
                         r'3.4.5 程序升温脱附（ $\mathrm{NH}_3$ -TPD， $\mathrm{CO}_2$ -TPD）')
        self.assertEqual(normalize_heading(r'Al_2O_3@Fe_3O_4'), r'Al₂O₃@Fe₃O₄')

    def test_normalize_prose_brackets_and_process(self):
        self.assertEqual(process(r'先验分布 $p(z)$ [通常为 $\mathcal{N}(0, I)$ ]。这种正则化'),
                         r'先验分布 $p(z)$ ［通常为 $\mathcal{N}(0, I)$ ］。这种正则化')
        self.assertEqual(process(r'信息[即 $M=(A_{\mathrm{asu}}, X_{\mathrm{asu}}, S, L)$ ，其中 $A_{\mathrm{asu}}$ ... L 为晶格参数]。在此基础上'),
                         r'信息［即 $M=(A_{\mathrm{asu}}, X_{\mathrm{asu}}, S, L)$ ，其中 $A_{\mathrm{asu}}$ ... L 为晶格参数］。在此基础上')
        self.assertEqual(process(r'潜空间模型[变分自编码器(VAE) $^{[17]}$ 和生成对抗网络(GAN) $^{[18-19]}$ ]、扩散/流模型'),
                         r'潜空间模型［变分自编码器(VAE) $^{[17]}$ 和生成对抗网络(GAN) $^{[18-19]}$ ］、扩散/流模型')
        self.assertEqual(process(r'方法之一[29-30]，该表示法'), r'方法之一[29-30]，该表示法')
        self.assertEqual(process(r'SMILES $^{[3]}$ 、国际化合物'), r'SMILES $^{[3]}$ 、国际化合物')
        self.assertEqual(process(r'![数据表原图](images/6DXKSD7N/abc.png)'), r'![数据表原图](images/6DXKSD7N/abc.png)')
        self.assertEqual(process(r'[链接文字](https://example.com)'), r'[链接文字](https://example.com)')
        self.assertEqual(process(r'[1] WALTERS W P, MURCKO M. Assessing'), r'[1] WALTERS W P, MURCKO M. Assessing')

    def test_stuck_vert_and_inequalities_normalized(self):
        self.assertEqual(normalize_math(r'\vertz_+ z_-\vert'), r'\vert z_+ z_-\vert')
        self.assertEqual(normalize_math(r'\vertj_1 - j_2\vert'), r'\vert j_1 - j_2\vert')
        self.assertEqual(normalize_math(r'x << R_c'), r'x \ll  R_c')
        self.assertEqual(normalize_math(r'x >> y'), r'x \gg  y')

    def test_extractor_shared_semantics_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'a.md').write_text('`$code$`\n![a $alt$](x)\n$x$\n$$y$$\n$bad\n', encoding='utf-8')
            records = extract(root)
            self.assertEqual([r['tex'] for r in records[:2]], ['x', 'y'])
            self.assertEqual([r['display'] for r in records[:2]], [False, True])
            self.assertEqual([r['line'] for r in records[:2]], [3, 4])
            self.assertEqual(records[2]['kind'], 'parse-error')

    def test_commonmark_continuations_list_footnotes_and_nested_fences(self):
        text = ('Prose\n    continuation $a$\n\n- list\n\n    continuation $b$\n\n'
                '[^n]: footnote $c$\n\n    continuation $d$\n\n'
                '> ~~~tex\n> $code$\n> ~~~\n\n    $indented-code$\n')
        spans, issues = scan_math(text)
        self.assertEqual([s.body for s in spans], ['a', 'b', 'c', 'd'])
        self.assertEqual(issues, [])
        from preview_server import render_markdown
        self.assertEqual(render_markdown(text).count('data-display='), 4)

    def test_prose_rewrite_preserves_all_existing_link_and_code_syntax(self):
        text = ('[[中文#标题|显示]] ![[图片.png|中文]] [中文][ref] [ref][] [ref]\n\n'
                '[ref]: target.md "title"\n\n`[中文 $x$]`\n\n'
                '> ~~~md\n> [中文 $x$]\n> ~~~\n\n    [中文 $x$]\n')
        self.assertEqual(normalize_prose_brackets(text), text)
        from markdown_resources import rewrite_unprotected
        rewritten = rewrite_unprotected(text + '\nFigure S1\n', lambda s: s.replace('Figure S1', 'CHANGED'))
        self.assertEqual(rewritten, text + '\nCHANGED\n')

    def test_python_and_node_structural_diagnostics_match(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node unavailable')
        bodies = [r'(x', r'[x]', r'\text{literal (}', r'[\text{Au(NO}_{3})_{3}]',
                  r'\left. x\right)', r'\tag{1}+x\tag{2}', r'\tag{13.}',
                  r'\text{\tag{1}}+x', r'x}', r'\{x\}', r'\\\tag{1}+x\tag{2}']
        script = 'const s=require(process.argv[1]);let x="";process.stdin.on("data",d=>x+=d);process.stdin.on("end",()=>console.log(JSON.stringify(JSON.parse(x).map(s.structuralIssues))));'
        run = subprocess.run([node, '-e', script, str(SCRIPTS / 'validate_math.cjs')],
                             input=json.dumps(bodies), capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), [math_issues(b) for b in bodies])


class KatexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which('node')
        if not cls.node:
            raise unittest.SkipTest('node is unavailable')
        cls.env = os.environ.copy()
        existing = SCRIPTS / 'node_modules'
        if existing.is_dir():
            cls.env['NODE_PATH'] = str(existing) + os.pathsep + cls.env.get('NODE_PATH', '')
        probe = subprocess.run([cls.node, '-e', "require('katex')"], env=cls.env, capture_output=True)
        if probe.returncode:
            raise unittest.SkipTest('existing KaTeX unavailable (no dependency installation)')

    def validate(self, records, *options):
        with tempfile.TemporaryDirectory() as tmp:
            target, report = Path(tmp) / 'formulas.json', Path(tmp) / 'custom-report.json'
            target.write_text(json.dumps(records), encoding='utf-8')
            result = subprocess.run([self.node, str(SCRIPTS / 'validate_math.cjs'), str(target),
                                     '--report', str(report), *options], env=self.env,
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertTrue(report.is_file(), result.stderr)
            self.assertFalse((Path(tmp) / 'math-validation.json').exists())
            return result, json.loads(report.read_text(encoding='utf-8'))

    def test_empty_default_failure_and_explicit_opt_in(self):
        self.assertEqual(self.validate([])[0].returncode, 1)
        self.assertEqual(self.validate([], '--allow-empty')[0].returncode, 0)

    def test_explicit_display_is_respected_for_tag(self):
        record = {'tex': r'x\tag{12.45}', 'display': False}
        self.assertEqual(self.validate([record])[0].returncode, 1)
        record['display'] = True
        result, report = self.validate([record])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(report['pass'], 1)
        self.assertIn('content accuracy', report['scope'])

    def test_unknown_multiple_and_truncated_fail_without_guessing(self):
        records = [{'tex': body, 'display': True} for body in
                   [r'\unknownOCRword', r'x\tag{1}+y\tag{2}', r'x\tag{13.}', r'\frac{x}']]
        result, report = self.validate(records)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report['failed'], 4)

    def test_real_legal_macros_and_nested_tag_compile(self):
        records = [{'tex': r'a\neq b, A\subseteq B, \int_0^1 x\,dx', 'display': False},
                   {'tex': r'x\tag*{(12.45)\(^{\text{①}}\)}', 'display': True},
                   {'tex': '$$y$$'}, {'tex': r'\[z\]'}]
        result, report = self.validate(records)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(report['pass'], 4)

    def test_parse_error_never_treated_as_empty_success(self):
        result, report = self.validate([{'tex': '$bad', 'kind': 'parse-error', 'errors': ['Unpaired $']}], '--allow-empty')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report['failed'], 1)


if __name__ == '__main__':
    unittest.main()
