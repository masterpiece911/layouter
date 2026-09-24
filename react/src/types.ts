import type { ReactNode } from "react";

export type Command = string[];

export interface OutputRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Output {
  name: string;
  active: boolean;
  primary?: boolean;
  scale?: number;
  rect: OutputRect;
}

export interface EvaluationContext {
  project: string;
  workflow: string;
  projectName: string;
  args: Record<string, string>;
  outputs: Output[];
}

export interface WorkflowProps {
  session?: string;
  cwd?: string;
  env?: Record<string, string>;
  focus?: string;
  description?: string;
  syncDisplays?: boolean;
  children?: ReactNode;
}

export type WorkspaceProps = ({ name: string; number?: number } | { name?: string; number: number }) & {
  layout?: "splith" | "splitv" | "tabbed" | "stacking";
  output?: string | string[];
  enabled?: boolean;
  children?: ReactNode;
}

export interface ContainerProps {
  name: string;
  layout: "splith" | "splitv" | "tabbed" | "stacking";
  size?: number;
  enabled?: boolean;
  children?: ReactNode;
}

export type FloatingGeometry = {
  floating: true;
  width?: number;
  height?: number;
  size?: never;
} & ({
  position?: "center" | "top" | "bottom" | "left" | "right" | "top-left" | "top-right" | "bottom-left" | "bottom-right";
  x?: never;
  y?: never;
} | {
  position?: never;
  x?: number;
  y?: number;
});
export type Placement = FloatingGeometry | {
  floating?: false;
  size?: number;
  x?: never;
  y?: never;
  width?: never;
  height?: never;
  position?: never;
};

export type WindowProps = Placement & {
  name: string;
  command: Command;
  match?: Record<string, string>;
  cwd?: string;
  env?: Record<string, string>;
  adopt?: boolean;
  enabled?: boolean;
}

export type KittyProps = Placement & {
  name: string;
  cwd?: string;
  env?: Record<string, string>;
  executable?: string;
  config?: string;
  options?: Record<string, string>;
  class?: string;
  session?: string;
  layout?: string;
  title?: string;
  enabled?: boolean;
  children?: ReactNode;
}

export interface TabProps {
  name: string;
  title?: string;
  layout?: string;
  cwd?: string;
  env?: Record<string, string>;
  enabled?: boolean;
  children?: ReactNode;
}

export interface PaneProps {
  name?: string;
  title?: string;
  command?: Command;
  cwd?: string;
  env?: Record<string, string>;
  hold?: boolean;
  after?: string;
  location?: string;
  enabled?: boolean;
}
