/** Compile temporary host nodes to the readable document; Python owns semantics. */
export type HostType = 'layouter-workflow' | 'layouter-workspace' | 'layouter-container'
  | 'layouter-window' | 'layouter-kitty' | 'layouter-tab' | 'layouter-pane';
export interface HostNode { type: HostType; props: Record<string, unknown>; children: HostNode[] }
export interface RootContainer { children: HostNode[] }
const children: Record<HostType, HostType[]> = {
  'layouter-workflow': ['layouter-workspace'],
  'layouter-workspace': ['layouter-window', 'layouter-kitty', 'layouter-container'],
  'layouter-container': ['layouter-window', 'layouter-kitty', 'layouter-container'],
  'layouter-kitty': ['layouter-tab', 'layouter-pane'],
  'layouter-tab': ['layouter-pane'], 'layouter-pane': [], 'layouter-window': [],
};

function compile(node: HostNode): Record<string, unknown> {
  if (!Object.hasOwn(children, node.type)) throw new Error(`Unknown host primitive: ${node.type}`);
  if (node.type === 'layouter-container' &&
      (typeof node.props.name !== 'string' || !node.props.name)) {
    throw new Error('Container requires an explicit name; React keys are not Layouter identities');
  }
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(node.props)) {
    if (key === 'children' || value === undefined) continue;
    if (key === 'floating' && ['layouter-window', 'layouter-kitty'].includes(node.type)) {
      if (typeof value !== 'boolean') throw new Error('floating must be a boolean');
      continue;
    }
    if (['workspace', 'window', 'kitty', 'container', 'pane', 'tab', 'order', 'args'].includes(key)) {
      throw new Error(`${node.type}: ${key} must be expressed through children or module metadata`);
    }
    result[key === 'syncDisplays' ? 'sync_displays' : key] = value;
  }
  let order = 0;
  for (const child of node.children) {
    if (!children[node.type].includes(child.type)) {
      throw new Error(`${node.type} cannot contain ${child.type}`);
    }
    const item = compile(child);
    const kind = child.type.slice('layouter-'.length);
    let destination = result;
    if (child.props.floating === true) {
      if (node.type !== 'layouter-workspace') {
        throw new Error('Floating children are only supported directly under a Workspace');
      }
      destination = (result.floating ??= {}) as Record<string, unknown>;
    } else if (node.type === 'layouter-workspace' || node.type === 'layouter-container') {
      item.order = order++;
    }
    ((destination[kind] ??= []) as unknown[]).push(item);
  }
  if (node.type === 'layouter-workflow') result.workspace ??= [];
  return result;
}

export function compileRoot(root: RootContainer): Record<string, unknown> {
  if (root.children.length !== 1 || root.children[0].type !== 'layouter-workflow') {
    throw new Error('A workflow must render exactly one <Workflow> root');
  }
  return compile(root.children[0]);
}
