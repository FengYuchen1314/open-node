export interface LicenseContract {
  edition: "free";
  licenseRequired: false;
  paidEntitlementsEnabled: false;
  externalLicenseServer: null;
  featureGates: string[];
}

export const licenseContract: LicenseContract = Object.freeze({
  edition: "free",
  licenseRequired: false,
  paidEntitlementsEnabled: false,
  externalLicenseServer: null,
  featureGates: [],
});
