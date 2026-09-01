export const announcementTypes = ["general", "maintenance", "sub_update"] as const;
export type AnnouncementType = typeof announcementTypes[number];

export interface Announcement {
  id: string;
  type: AnnouncementType;
  title: string;
  body: string;
  created_at: string;
  expires_at: string | null;
}

export interface AnnouncementsResponse {
  announcements: Announcement[];
  license_required: false;
}

export interface AnnouncementCreate {
  type: AnnouncementType;
  title: string;
  body: string;
  expires_minutes: number;
}

export function safeAnnouncementText(value: string, maximum: number, multiline = false) {
  const result = multiline ? value.replace(/\r\n?/g, "\n").trim() : value.trim();
  if ((!multiline && !result) || [...result].length > maximum) return null;
  if ([...result].some(char => {
    const code = char.codePointAt(0) ?? 0;
    return (code < 32 && !(multiline && (char === "\n" || char === "\t"))) || code === 127;
  })) return null;
  return result;
}
