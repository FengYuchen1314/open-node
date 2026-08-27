import { describe, expect, it } from "vitest";

import { licenseContract } from "./license";

describe("licenseContract", () => {
  it("keeps Open Node free of activation and paid entitlement checks", () => {
    expect(licenseContract.edition).toBe("free");
    expect(licenseContract.licenseRequired).toBe(false);
    expect(licenseContract.paidEntitlementsEnabled).toBe(false);
    expect(licenseContract.externalLicenseServer).toBeNull();
    expect(licenseContract.featureGates).toEqual([]);
  });
});
