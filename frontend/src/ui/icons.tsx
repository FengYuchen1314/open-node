import React from "react";

const glyphs: Record<string, string> = {
  ApartmentOutlined: "▦", ArrowDownOutlined: "↓", ArrowRightOutlined: "→", ArrowUpOutlined: "↑",
  BranchesOutlined: "⑂", CheckCircleOutlined: "●", CheckOutlined: "✓", CloseOutlined: "×",
  CloudDownloadOutlined: "⇩", ControlOutlined: "⌘", CopyOutlined: "⧉", DashboardOutlined: "◫",
  DeleteOutlined: "⌫", DownloadOutlined: "↓", EditOutlined: "✎", ExclamationCircleOutlined: "!",
  EyeOutlined: "◉", FileTextOutlined: "▤", FolderOpenOutlined: "▱", KeyOutlined: "⚿",
  LineChartOutlined: "⌁", LinkOutlined: "↗", LockOutlined: "▣", LoginOutlined: "↪",
  LogoutOutlined: "↩", MenuOutlined: "☰", PlayCircleOutlined: "▶", PlusOutlined: "+",
  ReloadOutlined: "↻", RollbackOutlined: "↶", SafetyCertificateOutlined: "◆", SafetyOutlined: "◇",
  SaveOutlined: "▣", SearchOutlined: "⌕", SendOutlined: "➤", SettingOutlined: "⚙",
  StopOutlined: "■", SyncOutlined: "↺", UndoOutlined: "↶", UploadOutlined: "↑",
  UserAddOutlined: "♙+", UserOutlined: "♙",
  ArrowLeftOutlined: "←", ClockCircleOutlined: "◷", FundOutlined: "▥", HomeOutlined: "⌂",
  ShareAltOutlined: "⑂", ThunderboltOutlined: "ϟ",
};

const make = (name: string) => ({ className, ...props }: Record<string, any>) => <span {...props} data-icon={glyphs[name] ?? "•"} className={["ui-icon", className].filter(Boolean).join(" ")} aria-hidden={props["aria-label"] ? undefined : true} />;

export const ApartmentOutlined = make("ApartmentOutlined");
export const ArrowDownOutlined = make("ArrowDownOutlined");
export const ArrowRightOutlined = make("ArrowRightOutlined");
export const ArrowUpOutlined = make("ArrowUpOutlined");
export const BranchesOutlined = make("BranchesOutlined");
export const CheckCircleOutlined = make("CheckCircleOutlined");
export const CheckOutlined = make("CheckOutlined");
export const CloseOutlined = make("CloseOutlined");
export const CloudDownloadOutlined = make("CloudDownloadOutlined");
export const ControlOutlined = make("ControlOutlined");
export const CopyOutlined = make("CopyOutlined");
export const DashboardOutlined = make("DashboardOutlined");
export const DeleteOutlined = make("DeleteOutlined");
export const DownloadOutlined = make("DownloadOutlined");
export const EditOutlined = make("EditOutlined");
export const ExclamationCircleOutlined = make("ExclamationCircleOutlined");
export const EyeOutlined = make("EyeOutlined");
export const FileTextOutlined = make("FileTextOutlined");
export const FolderOpenOutlined = make("FolderOpenOutlined");
export const KeyOutlined = make("KeyOutlined");
export const LineChartOutlined = make("LineChartOutlined");
export const LinkOutlined = make("LinkOutlined");
export const LockOutlined = make("LockOutlined");
export const LoginOutlined = make("LoginOutlined");
export const LogoutOutlined = make("LogoutOutlined");
export const MenuOutlined = make("MenuOutlined");
export const PlayCircleOutlined = make("PlayCircleOutlined");
export const PlusOutlined = make("PlusOutlined");
export const ReloadOutlined = make("ReloadOutlined");
export const RollbackOutlined = make("RollbackOutlined");
export const SafetyCertificateOutlined = make("SafetyCertificateOutlined");
export const SafetyOutlined = make("SafetyOutlined");
export const SaveOutlined = make("SaveOutlined");
export const SearchOutlined = make("SearchOutlined");
export const SendOutlined = make("SendOutlined");
export const SettingOutlined = make("SettingOutlined");
export const StopOutlined = make("StopOutlined");
export const SyncOutlined = make("SyncOutlined");
export const UndoOutlined = make("UndoOutlined");
export const UploadOutlined = make("UploadOutlined");
export const UserAddOutlined = make("UserAddOutlined");
export const UserOutlined = make("UserOutlined");
export const ArrowLeftOutlined = make("ArrowLeftOutlined");
export const ClockCircleOutlined = make("ClockCircleOutlined");
export const FundOutlined = make("FundOutlined");
export const HomeOutlined = make("HomeOutlined");
export const ShareAltOutlined = make("ShareAltOutlined");
export const ThunderboltOutlined = make("ThunderboltOutlined");
