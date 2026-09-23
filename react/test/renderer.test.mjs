import test from 'node:test';
import assert from 'node:assert/strict';
import React, { createContext, useContext, useMemo, useState } from 'react';
import { evaluateWorkflow } from '../dist/renderer.js';
import { Workflow, Workspace, Container, Window, Kitty, Pane, Tab,
  useArg, useWorkflowArgs, useScreen, useOutputs, usePrimaryOutput, useOutput } from '../dist/index.js';
const h = React.createElement;
const context = { project: '/project', projectName: 'project', workflow: 'dev', args: { mode: 'prod' }, outputs: [] };
const window = name => h(Window, { name, command: ['app'] });
const root = (...children) => h(Workflow, {}, h(Workspace, { name: 'code' }, ...children));

test('real function components, fragments, arrays, context, state and argument hooks', () => {
  const Custom = createContext('default');
  function Nested() {
    const [suffix] = useState('state');
    const prefix = useContext(Custom);
    const mode = useArg('mode');
    assert.equal(useWorkflowArgs().mode, 'prod');
    const names = useMemo(() => [prefix, mode, suffix], [prefix, mode, suffix]);
    return h(React.Fragment, {}, names.map(name => h(Window, { key: `react-${name}`, name, command: ['app'] })), false, null);
  }
  const result = evaluateWorkflow(h(Custom.Provider, { value: 'custom' }, root(h(Nested))), context);
  assert.deepEqual(result.workspace[0].window.map(n => [n.name, n.order]), [['custom', 0], ['prod', 1], ['state', 2]]);
  assert.ok(result.workspace[0].window.every(n => !('key' in n)));
});

test('mixed ordering at every hierarchy and floating representation', () => {
  const result = evaluateWorkflow(root(window('editor'), h(Kitty, { name: 'terminal' }, h(Pane, { name: 'shell' })),
    h(Container, { name: 'tools', layout: 'splitv' }, window('first'),
      h(Kitty, { name: 'tabs' }, h(Tab, { name: 'main' }, h(Pane, { name: 'p' }))), window('last')),
    window('browser'), h(Kitty, { name: 'float', floating: true, position: 'center', width: 1000 }, h(Pane, { name: 'shell' }))), context);
  const ws = result.workspace[0];
  assert.deepEqual(ws.window.map(w => w.order), [0, 3]);
  assert.equal(ws.kitty[0].order, 1);
  assert.equal(ws.container[0].order, 2);
  assert.deepEqual(ws.container[0].window.map(w => w.order), [0, 2]);
  assert.equal(ws.container[0].kitty[0].order, 1);
  assert.deepEqual(ws.floating.kitty[0], { name: 'float', position: 'center', width: 1000, pane: [{ name: 'shell' }] });
});

test('output hooks respond to the supplied snapshot, with deterministic fallback', () => {
  function Responsive() {
    const screen = useScreen();
    assert.equal(useScreen('missing').width, 0);
    assert.equal(useOutputs().length, context.outputs.length);
    assert.equal(useOutput('DP-1'), usePrimaryOutput());
    return h(Workflow, {}, h(Workspace, { name: 'code', layout: screen.width >= 1920 ? 'splith' : 'splitv' }));
  }
  assert.equal(evaluateWorkflow(h(Responsive), context).workspace[0].layout, 'splitv');
  context.outputs = [{ name: 'off', active: false, primary: true, rect: { width: 100 } },
    { name: 'DP-1', active: true, rect: { x: 0, y: 0, width: 2560, height: 1440 } }];
  assert.equal(evaluateWorkflow(h(Responsive), context).workspace[0].layout, 'splith');
  context.outputs = [];
});

test('friendly diagnostics for invalid host trees and component errors', () => {
  for (const [element, message] of [
    [root('text'), /Text nodes/],
    [root(h(Pane, { name: 'p' })), /cannot contain/],
    [root(h(Container, { key: 'not-a-name', layout: 'splith' })), /explicit name/],
    [root(h(Container, { name: 'c', layout: 'splith' }, h(Window, { name: 'f', floating: true }))), /directly under/],
    [root(h(Window, { name: 'w', command: ['app'], floating: 'yes' })), /boolean/],
    [h(React.Fragment, {}, root(), root()), /exactly one/],
    [root(h(() => { throw new Error('component exploded'); })), /component exploded/],
    [root(h(() => { useArg('missing'); return null; })), /Unknown workflow argument/],
  ]) assert.throws(() => evaluateWorkflow(element, context), message);
});
