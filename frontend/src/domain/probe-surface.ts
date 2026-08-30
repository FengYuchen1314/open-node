export interface ProbeSurfaceStartup {
  refreshPublicProbe: () => void;
  refreshPublicTargets: () => void;
  refreshAdministratorTasks: () => void;
  openPublicStream: () => void;
}

export function allowsProbeAdministratorAccess(publicOnly: boolean): boolean {
  return !publicOnly;
}

export function browserProbeAccessToken(publicOnly: boolean, token: string): string | undefined {
  if (publicOnly) {
    return undefined;
  }
  return token.trim() || undefined;
}

export function startProbeSurface(publicOnly: boolean, startup: ProbeSurfaceStartup): void {
  startup.refreshPublicProbe();
  startup.refreshPublicTargets();
  if (allowsProbeAdministratorAccess(publicOnly)) {
    startup.refreshAdministratorTasks();
  }
  startup.openPublicStream();
}
