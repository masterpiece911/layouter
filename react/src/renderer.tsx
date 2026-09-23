/*
 * Thin, in-memory React renderer.
 *
 * React reconciles only HostNode objects. Nothing in this file knows about
 * Sway/i3 IPC and no host operation has an external side effect.
 *
 * react-reconciler is intentionally isolated here because its host-config API
 * is experimental. If it changes, the rest of the JSX API and compiler stay
 * untouched.
 */
// @ts-nocheck
import React, { createContext } from "react";
import ReactReconciler from "react-reconciler";
import {
  ConcurrentRoot,
  DefaultEventPriority,
  NoEventPriority,
} from "react-reconciler/constants.js";

import { EvaluationContextProvider } from "./context.js";
import { compileRoot, type HostNode, type HostType, type RootContainer } from "./compiler.js";
import type { EvaluationContext } from "./types.js";

let currentUpdatePriority = NoEventPriority;

function propsWithoutChildren(props: Record<string, unknown>) {
  const { children: _children, ...rest } = props;
  return rest;
}

function append(parent: { children: HostNode[] }, child: HostNode) {
  remove(parent, child);
  parent.children.push(child);
}

function remove(parent: { children: HostNode[] }, child: HostNode) {
  const index = parent.children.indexOf(child);
  if (index !== -1) parent.children.splice(index, 1);
}

function insertBefore(
  parent: { children: HostNode[] },
  child: HostNode,
  before: HostNode,
) {
  remove(parent, child);
  const index = parent.children.indexOf(before);
  if (index === -1) parent.children.push(child);
  else parent.children.splice(index, 0, child);
}

const reconciler = ReactReconciler({
  noTimeout: -1,
  isPrimaryRenderer: true,
  supportsMutation: true,
  supportsPersistence: false,
  supportsHydration: false,
  supportsMicrotasks: true,

  createInstance(type: HostType, props: Record<string, unknown>): HostNode {
    return {
      type,
      props: propsWithoutChildren(props),
      children: [],
    };
  },

  appendInitialChild: append,
  appendChild: append,
  appendChildToContainer: append,
  removeChild: remove,
  removeChildFromContainer: remove,
  insertBefore,
  insertInContainerBefore: insertBefore,

  finalizeInitialChildren() {
    return false;
  },

  createTextInstance(text: string) {
    throw new Error(
      `Text nodes are not meaningful in a Layouter workflow (${JSON.stringify(text)}). ` +
      "Put values in props instead.",
    );
  },

  getPublicInstance(instance: HostNode) {
    return instance;
  },

  prepareForCommit() {
    return null;
  },

  resetAfterCommit() {},
  resetTextContent() {},
  commitTextUpdate() {},

  commitUpdate(
    instance: HostNode,
    _type: HostType,
    _oldProps: Record<string, unknown>,
    newProps: Record<string, unknown>,
  ) {
    instance.props = propsWithoutChildren(newProps);
  },

  commitMount() {},

  shouldSetTextContent() {
    return false;
  },

  getRootHostContext() {
    return {};
  },

  getChildHostContext() {
    return {};
  },

  scheduleTimeout: setTimeout,
  cancelTimeout: clearTimeout,
  preparePortalMount() {},

  scheduleMicrotask(callback: () => void) {
    queueMicrotask(callback);
  },

  clearContainer(container: RootContainer) {
    container.children = [];
  },

  setCurrentUpdatePriority(priority: number) {
    currentUpdatePriority = priority;
  },

  getCurrentUpdatePriority() {
    return currentUpdatePriority;
  },

  resolveUpdatePriority() {
    return currentUpdatePriority !== NoEventPriority
      ? currentUpdatePriority
      : DefaultEventPriority;
  },

  shouldAttemptEagerTransition() {
    return false;
  },

  requestPostPaintCallback() {},
  trackSchedulerEvent() {},
  resolveEventType() {
    return null;
  },
  resolveEventTimeStamp() {
    return -1.1;
  },

  maySuspendCommit() {
    return false;
  },
  preloadInstance() {
    return true;
  },
  startSuspendingCommit() {},
  suspendInstance() {},
  waitForCommitToBeReady() {
    return null;
  },

  resetFormInstance() {},
  NotPendingTransition: null,
  HostTransitionContext: createContext(null),

  getInstanceFromNode() {
    return null;
  },
  getInstanceFromScope() {
    return null;
  },

  beforeActiveInstanceBlur() {},
  afterActiveInstanceBlur() {},
  prepareScopeUpdate() {},
  detachDeletedInstance() {},
});

function createReactRoot(container: RootContainer, onError: (error: Error) => void) {
  return reconciler.createContainer(
    container,
    ConcurrentRoot,
    null,
    false,
    null,
    "layouter_",
    onError,
    onError,
    onError,
    () => undefined,
  );
}

export function evaluateWorkflow(
  element: React.ReactElement,
  context: EvaluationContext,
): Record<string, unknown> {
  const container: RootContainer = { children: [] };
  let error: Error | undefined;
  const root = createReactRoot(container, value => { error = value; });

  const wrapped = (
    <EvaluationContextProvider value={context}>
      {element}
    </EvaluationContextProvider>
  );

  if (typeof reconciler.updateContainerSync !== "function") {
    throw new Error(
      "Incompatible React runtime: updateContainerSync is unavailable.",
    );
  }

  reconciler.updateContainerSync(wrapped, root, null, () => undefined);
  reconciler.flushSyncWork();

  try {
    if (error) throw error;
    return compileRoot(container);
  } finally {
    reconciler.updateContainerSync(null, root, null, () => undefined);
    reconciler.flushSyncWork();
  }
}
