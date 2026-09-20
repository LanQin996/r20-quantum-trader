/**
 * 全站所有 BaseDialog 显式 title 属性全覆盖与模型编辑保存门禁守卫闸（批 130）。
 *
 * ## 实测缺陷与背景：
 *
 * 1. `BaseDialog` 标头属性覆盖率（WCAG 4.1.2 可访问名称）：
 *    在 `BaseDialog.vue` 中，对话框容器通过 `:aria-labelledby="title || $slots.title ? titleId : undefined"`
 *    将对话框的无障碍名称锚定到标题。此前全站 21 处 BaseDialog 中：
 *    `AboutModal.vue` 与 `InterceptorsPage.vue` 源码编辑器两处仅使用了 `<template #title>` 插槽，
 *    未显式传递 `:title` prop，导致组件在 props 检查或外部透传时无法直接读取对话框名称。
 *    补齐后：全仓全部 21 处 BaseDialog 100% 具备显式 `:title` 或 `title` 属性。
 *
 * 2. `ModelEditDialog.vue` 模型保存按钮缺乏必填 ID 门禁：
 *    模型 ID 为大模型实例的唯一主键（PK），若 ID 为空则向后端派发必然失败的无效请求。
 *    此前提交按钮全裸可点，现补齐 `:disabled="!modelForm.id.trim()"`。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(import.meta.dirname, '..', 'src');

function getVueFiles(dir, out = []) {
  for (const n of readdirSync(dir)) {
    const p = path.join(dir, n);
    if (statSync(p).isDirectory()) getVueFiles(p, out);
    else if (n.endsWith('.vue')) out.push(p);
  }
  return out;
}

test('全仓全部 21 个 BaseDialog 必须 100% 具备显式 title 或 :title 属性', () => {
  const files = getVueFiles(SRC);
  const badDialogs = [];
  let totalDialogs = 0;

  for (const f of files) {
    const rel = path.relative(SRC, f);
    const content = readFileSync(f, 'utf8');
    const matches = content.matchAll(/<BaseDialog\b([^>]*?)>/gs);
    for (const m of matches) {
      totalDialogs++;
      const attrs = m[1];
      const hasTitle = /\b:?title=["']/.test(attrs);
      if (!hasTitle) {
        badDialogs.push(`${rel} 中的 <BaseDialog> 缺少 title/:title 属性`);
      }
    }
  }

  assert.ok(totalDialogs >= 20, `检索到的 BaseDialog 总数过少: ${totalDialogs}`);
  assert.deepEqual(
    badDialogs,
    [],
    `发现缺少 title 属性的 BaseDialog 弹窗：\n  ${badDialogs.join('\n  ')}`,
  );
});

test('ModelEditDialog 保存模型按钮必须受 modelForm.id.trim() 前置门禁保护', () => {
  const vue = readFileSync(path.join(SRC, 'views/admin/llm/ModelEditDialog.vue'), 'utf8');
  assert.match(
    vue,
    /<button[^>]*type="submit"[^>]*:disabled="!modelForm\.id\.trim\(\)"/,
    'ModelEditDialog 保存按钮缺少 :disabled="!modelForm.id.trim()" 门禁',
  );
});
