/**
 * 提示词工坊与物理拦截器代码编辑器尺寸与等宽字体守卫闸（批 131）。
 *
 * ## 实测缺陷与背景：
 *
 * 1. 提示词工坊（PromptStudioPage）：
 *    此前单模块编辑器高度仅 `rows="12"`（实际测量高度仅约 248px），且使用了普通非等宽正文字体
 *    （font-family: inherit），在桌面大屏上呈现为一个极其局促、别扭的小输入框，无法胜任长篇
 *    提示词工程和 Markdown 规则的编排与审阅。
 *    优化后：textarea 行数扩至 24 行，设置 min-height >= 500px，统一采用 var(--ds-font-mono)
 *    并挂载代码块背景色，右侧预览同步维持 500px 均衡高度。
 *
 * 2. 物理拦截器源码编辑器（InterceptorsPage）：
 *    此前弹窗最大宽度仅 960px（size="xl"），代码框高度仅约 440px，面对 100~200 行 Python
 *    风控逻辑时极其狭窄。
 *    优化后：BaseDialog 扩充支持 2xl（1160px 宽度），编辑器弹窗升级为 size="2xl"，代码框 min-height
 *    扩充至 >= 520px，支持 4 空格缩进（tab-size: 4）与专业代码编辑器配色。
 *
 * 3. 投委会席位提示词（CouncilPage）：
 *    参谋席位系统提示词同样扩展至 rows="20"，min-height >= 400px，统一采用等宽字体。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(import.meta.dirname, '..', 'src');

test('PromptStudioPage 提示词主编辑区必须具备宽敞高度与等宽字体排版', () => {
  const vue = readFileSync(path.join(SRC, 'views/admin/PromptStudioPage.vue'), 'utf8');

  // 行数检查
  const rowsMatch = vue.match(/<textarea[^>]*class="[^"]*ps-textarea[^"]*"[^>]*rows="(\d+)"/) ||
                    vue.match(/<textarea[^>]*rows="(\d+)"[^>]*class="[^"]*ps-textarea[^"]*"/);
  assert.ok(rowsMatch, '未找到 ps-textarea 元素');
  const rows = parseInt(rowsMatch[1], 10);
  assert.ok(rows >= 20, `ps-textarea 行数过小 (${rows} < 20)，必须为长提示词预留宽敞视口`);

  // CSS 样式检查
  assert.match(
    vue,
    /\.ps-textarea\s*\{[^}]*min-height:\s*5\d\dpx/,
    'ps-textarea CSS 缺少 min-height >= 500px 声明',
  );
  assert.match(
    vue,
    /\.ps-textarea\s*\{[^}]*font-family:\s*var\(--ds-font-mono\)/,
    'ps-textarea 必须使用 var(--ds-font-mono) 等宽字体',
  );
});

test('InterceptorsPage 拦截器源码编辑器必须采用 2xl 超宽弹窗与宽敞 Python 代码编辑区', () => {
  const vue = readFileSync(path.join(SRC, 'views/admin/InterceptorsPage.vue'), 'utf8');

  // 弹窗尺寸检查
  assert.match(
    vue,
    /<BaseDialog[^>]*:open="editorVisible"[^>]*size="2xl"/,
    'InterceptorsPage 源码编辑弹窗必须使用 size="2xl" 展开 1160px 宽敞视口',
  );

  // 代码框样式检查
  assert.match(
    vue,
    /\.ip-code\s*\{[^}]*min-height:\s*5\d\dpx/,
    'ip-code CSS 缺少 min-height >= 500px 声明',
  );
  assert.match(
    vue,
    /\.ip-code\s*\{[^}]*tab-size:\s*4/,
    'ip-code 必须设置 tab-size: 4 符合 Python 代码缩进规范',
  );
});

test('CouncilPage 席位提示词编辑区与 BaseDialog 2xl 规范守卫', () => {
  const councilVue = readFileSync(path.join(SRC, 'views/admin/CouncilPage.vue'), 'utf8');
  const baseDialogVue = readFileSync(path.join(SRC, 'components/base/BaseDialog.vue'), 'utf8');

  // CouncilPage 检查
  assert.match(
    councilVue,
    /\.cn-textarea\s*\{[^}]*min-height:\s*[34]\d\dpx/,
    'cn-textarea CSS 缺少 min-height >= 380px 声明',
  );
  assert.match(
    councilVue,
    /\.cn-textarea\s*\{[^}]*font-family:\s*var\(--ds-font-mono\)/,
    'cn-textarea 必须使用 var(--ds-font-mono) 等宽字体',
  );

  // BaseDialog 2xl 检查
  assert.match(
    baseDialogVue,
    /'2xl':\s*'1160px'/,
    'BaseDialog 必须支持 2xl (1160px) 扩展尺寸',
  );
});
