import React from "react";

const paths: Record<string, string[]> = {
  ApartmentOutlined: ["M4 21V7l8-4 8 4v14", "M9 21v-5h6v5", "M8 9h.01M12 9h.01M16 9h.01M8 12h.01M12 12h.01M16 12h.01"],
  ArrowDownOutlined: ["M12 5v14", "m19 12-7 7-7-7"],
  ArrowRightOutlined: ["M5 12h14", "m12 5 7 7-7 7"],
  ArrowUpOutlined: ["M12 19V5", "m5 12 7-7 7 7"],
  ArrowLeftOutlined: ["M19 12H5", "m12 19-7-7 7-7"],
  BranchesOutlined: ["M6 3v12", "M18 9a3 3 0 1 0-3-3", "M6 9h9", "M6 21a3 3 0 1 0 0-6"],
  CheckCircleOutlined: ["M22 11.1V12a10 10 0 1 1-5.9-9.1", "m9 11 3 3L22 4"],
  CheckOutlined: ["m5 12 4 4L19 6"],
  CloseOutlined: ["M18 6 6 18M6 6l12 12"],
  CloudDownloadOutlined: ["M12 13v8", "m8 17 4 4 4-4", "M20.4 17.5A5 5 0 0 0 18 8.2 7 7 0 0 0 4.3 10.5 4.5 4.5 0 0 0 5.5 19H7"],
  ControlOutlined: ["M4 6h16M4 12h16M4 18h16", "M8 4v4M16 10v4M10 16v4"],
  CopyOutlined: ["M8 8h11v11H8z", "M5 16H3V3h13v2"],
  DashboardOutlined: ["M4 4h6v6H4zM14 4h6v10h-6zM4 14h6v6H4zM14 18h6v2h-6z"],
  DeleteOutlined: ["M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14M10 11v6M14 11v6"],
  DownloadOutlined: ["M12 3v12", "m7 10 5 5 5-5", "M5 21h14"],
  EditOutlined: ["M12 20h9", "M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4z"],
  ExclamationCircleOutlined: ["M12 8v5M12 17h.01", "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0"],
  EyeOutlined: ["M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12", "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6"],
  FileTextOutlined: ["M6 2h8l4 4v16H6z", "M14 2v5h5M9 12h6M9 16h6"],
  FolderOpenOutlined: ["M3 6h7l2 2h9l-2 11H4z", "M3 6v13"],
  KeyOutlined: ["M15 7a4 4 0 1 1-3.7 5.5L3 21v-4l2-2h3l1.3-1.3", "M18 6h.01"],
  LineChartOutlined: ["M4 19V5M4 19h16", "m7 15 4-4 3 2 5-6"],
  LinkOutlined: ["M10 13a5 5 0 0 0 7.1.1l2-2a5 5 0 0 0-7.1-7.1l-1.1 1.1", "M14 11a5 5 0 0 0-7.1-.1l-2 2A5 5 0 0 0 12 20l1.1-1.1"],
  LockOutlined: ["M5 10h14v11H5z", "M8 10V7a4 4 0 0 1 8 0v3M12 14v3"],
  LoginOutlined: ["M10 17l5-5-5-5M15 12H3", "M14 3h7v18h-7"],
  LogoutOutlined: ["M14 8l4 4-4 4M18 12H8", "M10 4H4v16h6"],
  MenuOutlined: ["M4 7h16M4 12h16M4 17h16"],
  PlayCircleOutlined: ["M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0", "m10 8 6 4-6 4z"],
  PlusOutlined: ["M12 5v14M5 12h14"],
  ReloadOutlined: ["M20 7v5h-5", "M19 12a7 7 0 1 1-2-5"],
  RollbackOutlined: ["M9 7 4 12l5 5", "M5 12h9a6 6 0 0 1 6 6v1"],
  SafetyCertificateOutlined: ["M12 3 5 6v5c0 4.5 2.8 7.5 7 10 4.2-2.5 7-5.5 7-10V6z", "m9 12 2 2 4-5"],
  SafetyOutlined: ["M12 3 5 6v5c0 4.5 2.8 7.5 7 10 4.2-2.5 7-5.5 7-10V6z"],
  SaveOutlined: ["M5 3h12l2 2v16H5z", "M8 3v6h8V3M8 21v-7h8v7"],
  SearchOutlined: ["M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14", "m16 16 5 5"],
  SendOutlined: ["m3 11 18-8-8 18-2-7z", "m11 14 10-11"],
  SettingOutlined: ["M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6", "M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21h-4v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3v-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.5V3h4v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.5 1h.1v4h-.1a1.7 1.7 0 0 0-1.5 1"],
  StopOutlined: ["M6 6h12v12H6z"],
  SyncOutlined: ["M20 7v5h-5M4 17v-5h5", "M19 12a7 7 0 0 0-12-5M5 12a7 7 0 0 0 12 5"],
  UndoOutlined: ["M9 7 4 12l5 5", "M5 12h9a6 6 0 0 1 6 6"],
  UploadOutlined: ["M12 21V9", "m7 14 5-5 5 5", "M5 3h14"],
  UserAddOutlined: ["M15 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2", "M8.5 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M19 8v6M16 11h6"],
  UserOutlined: ["M20 21v-2a6 6 0 0 0-6-6h-4a6 6 0 0 0-6 6v2", "M12 9a4 4 0 1 0 0-8 4 4 0 0 0 0 8"],
  ClockCircleOutlined: ["M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0", "M12 7v5l3 2"],
  FundOutlined: ["M4 19V5M4 19h16", "m7 15 4-5 3 3 5-7"],
  HomeOutlined: ["m3 11 9-8 9 8", "M5 10v11h14V10M9 21v-6h6v6"],
  ShareAltOutlined: ["M18 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6M6 15a3 3 0 1 0 0 6 3 3 0 0 0 0-6M18 22a3 3 0 1 0 0-6 3 3 0 0 0 0 6", "m8.6 13.5 6.8-4M8.6 16.5l6.8 4"],
  ThunderboltOutlined: ["m13 2-9 12h7l-1 8 9-12h-7z"],
};

function make(name: string) {
  return function Icon({ className, ...props }: Record<string, any>) {
    const labelled = Boolean(props["aria-label"]);
    return <svg {...props} className={["ui-icon", className].filter(Boolean).join(" ")} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" role={labelled ? "img" : undefined} aria-hidden={labelled ? undefined : true}>
      {(paths[name] ?? ["M5 12h14"]).map((path, index) => <path d={path} key={index} />)}
    </svg>;
  };
}

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
