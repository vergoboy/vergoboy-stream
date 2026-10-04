"use client";

import { useState } from "react";
import { Link2, Archive, Radio } from "lucide-react";
import { AddVideoPanel } from "./AddVideoPanel";
import { ArchivePanel } from "./ArchivePanel";
import { LivePanel } from "./LivePanel";

type Tab = "add" | "archive" | "live";

/** Everything that puts something on the screen, in one place. */
export function AddHub({
  myName,
  canAdd,
  canManage,
  onLiveStarted,
}: {
  myName: string;
  canAdd: boolean;
  canManage: boolean;
  onLiveStarted: () => void;
}) {
  const [tab, setTab] = useState<Tab>("add");
  const [archiveLink, setArchiveLink] = useState<string | null>(null);
  const tabs: [Tab, React.ReactNode, string][] = [
    ["add", <Link2 key="a" className="h-4 w-4" />, "لینک و فایل"],
    ["archive", <Archive key="b" className="h-4 w-4" />, "آرشیو"],
    ["live", <Radio key="c" className="h-4 w-4" />, "پخش زنده"],
  ];
  return (
    <div>
      <div className="sticky top-0 z-10 -mx-1 mb-4 grid grid-cols-3 gap-1 rounded-2xl bg-[#1b1220] p-1">
        {tabs.map(([id, icon, label]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`flex items-center justify-center gap-1.5 rounded-xl py-2.5 text-[12.5px] font-bold transition-colors ${
              tab === id ? "bg-[color:var(--color-amber)] text-black" : "text-white/65 hover:bg-white/8"
            }`}
          >
            {icon}
            {label}
          </button>
        ))}
      </div>
      {tab === "add" && (
        <AddVideoPanel
          myName={myName}
          onArchiveLink={(u) => {
            setArchiveLink(u);
            setTab("archive");
          }}
        />
      )}
      {tab === "archive" && <ArchivePanel myName={myName} initialLink={archiveLink} />}
      {tab === "live" && <LivePanel myName={myName} canAdd={canAdd} canManage={canManage} onLiveStarted={onLiveStarted} />}
    </div>
  );
}
