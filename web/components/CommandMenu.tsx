"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { Command } from "cmdk";
import { Download, FilePlus2, FolderOpen, Moon, Play, Search } from "lucide-react";
import type { RequirementSetSummary, TestCase } from "@/lib/types";

export interface Action {
  id: string;
  label: string;
  run: () => void;
  disabled?: boolean;
}

/** Ctrl/Cmd-K: jump to a suite or a test case, or run an action, without the mouse. */
export function CommandMenu({ open, onOpenChange, sets, cases, onSet, onCase, actions }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  sets: RequirementSetSummary[];
  cases: TestCase[];
  onSet: (id: string) => void;
  onCase: (id: string) => void;
  actions: Action[];
}) {
  const close = (fn: () => void) => () => { onOpenChange(false); fn(); };
  const icon = (id: string) =>
    id.startsWith("run") ? <Play size={15} /> : id.startsWith("generate") ? <FilePlus2 size={15} /> :
    id.startsWith("theme") ? <Moon size={15} /> : <Download size={15} />;
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="cmdk-overlay" />
        <Dialog.Content className="cmdk-dialog" aria-describedby={undefined}>
          <Dialog.Title className="sr-only">Command menu</Dialog.Title>
          <Command label="Command menu">
            <div style={{ display: "flex", alignItems: "center", paddingLeft: 14 }}>
              <Search size={16} className="muted" aria-hidden />
              <Command.Input placeholder="Search suites, test cases and actions" />
            </div>
            <Command.List>
              <Command.Empty>Nothing matches. Try a case ID like TC004 or a requirement like AC-2.3.</Command.Empty>
              <Command.Group heading="Actions">
                {actions.filter((a) => !a.disabled).map((a) => (
                  <Command.Item key={a.id} value={a.label} onSelect={close(a.run)}>{icon(a.id)}{a.label}</Command.Item>
                ))}
              </Command.Group>
              <Command.Group heading="Suites">
                {sets.map((s) => (
                  <Command.Item key={s.id} value={`suite ${s.source_name}`} onSelect={close(() => onSet(s.id))}>
                    <FolderOpen size={15} />{s.source_name}<span className="muted small" style={{ marginLeft: "auto" }}>{s.case_count} cases</span>
                  </Command.Item>
                ))}
              </Command.Group>
              {cases.length > 0 && (
                <Command.Group heading="Test cases in this suite">
                  {cases.map((c) => (
                    <Command.Item key={c.id} value={`${c.id.split("-").pop()} ${c.requirement_ref} ${c.title}`} onSelect={close(() => onCase(c.id))}>
                      <span className="mono muted">{c.id.split("-").pop()}</span>{c.title}
                    </Command.Item>
                  ))}
                </Command.Group>
              )}
            </Command.List>
          </Command>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
