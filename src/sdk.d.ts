// Minimal ambient types for the parts of `@hermes/plugin-sdk` this plugin uses (typecheck only; the
// host supplies the real module at runtime). Shapes follow the Hermes Desktop source:
// apps/desktop/src/contrib/plugin.ts (context, contribution, os) and
// apps/desktop/src/lib/tool-media-renderers.ts (the tool media renderer area and its props).
import type { ReactNode } from 'react'

export interface PluginRestOptions {
  method?: string
  body?: unknown
  timeoutMs?: number
}

export interface PluginOs {
  /** Open a URL with the OS default handler. Resolves false when the shell can't. */
  openExternal: (url: string) => Promise<boolean>
}

export interface PluginContribution {
  id: string
  area: string
  order?: number
  data?: unknown
  render?: () => ReactNode
}

export interface PluginContext {
  readonly source: string
  register: (c: PluginContribution) => () => void
  registerMany: (cs: PluginContribution[]) => () => void
  onDispose: (fn: () => void) => void
  /** REST to this plugin's own backend namespace (`/api/plugins/<id>`). Rejects on HTTP >= 400. */
  rest: <T>(path: string, opts?: PluginRestOptions) => Promise<T>
  os: PluginOs
}

export interface HermesPlugin {
  id: string
  name?: string
  description?: string
  defaultEnabled?: boolean
  register: (ctx: PluginContext) => void
}

export type MediaKind = 'audio' | 'file' | 'image' | 'video' | (string & {})

/** The file a tool produced, as the transcript's `MEDIA:` line names it. */
export interface ToolMediaFile {
  /** Path on the backend's machine. Never open it directly. */
  path: string
  name: string
  kind: MediaKind
  mime: string
  resolveUrl: () => Promise<string>
}

export interface ToolMediaRendererProps {
  file: ToolMediaFile
  toolName: string
  toolCallId: string
  args: Readonly<Record<string, unknown>>
  /** The call's result: an object, or a string (a JSON string live, maybe parsed after a reload). */
  result: unknown
  sessionId: null | string
  /** The built-in player for this file: return it for anything not drawn. */
  fallback: ReactNode
}

export interface ToolMediaRendererContribution {
  tools: readonly string[]
  render: (props: ToolMediaRendererProps) => ReactNode
}

/** `'transcript.toolMedia'`. Older Desktops do not export it: feature-detect before use. */
export const TOOL_MEDIA_RENDERER_AREA: string | undefined
