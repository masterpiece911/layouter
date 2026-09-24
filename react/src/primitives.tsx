import React from "react";
import type {
  ContainerProps,
  KittyProps,
  PaneProps,
  TabProps,
  WindowProps,
  WorkflowProps,
  WorkspaceProps,
} from "./types.js";

export function Workflow(props: WorkflowProps) {
  return React.createElement("layouter-workflow", props);
}

export function Workspace(props: WorkspaceProps) {
  return React.createElement<WorkspaceProps>("layouter-workspace", props);
}

export function Container(props: ContainerProps) {
  return React.createElement("layouter-container", props);
}

export function Window(props: WindowProps) {
  return React.createElement<WindowProps>("layouter-window", props);
}

export function Kitty(props: KittyProps) {
  return React.createElement<KittyProps>("layouter-kitty", props);
}

export function Tab(props: TabProps) {
  return React.createElement("layouter-tab", props);
}

export function Pane(props: PaneProps) {
  return React.createElement("layouter-pane", props);
}
