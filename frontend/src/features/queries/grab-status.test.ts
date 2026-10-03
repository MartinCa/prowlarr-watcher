import { describe, expect, it } from "vitest";
import { deriveGrabStatus, type GrabState } from "@/features/queries/grab-status";

const idle: GrabState = {
  requestPending: false,
  requestFailed: false,
  jobId: undefined,
  jobStatus: undefined,
  pollFailed: false,
};

describe("deriveGrabStatus", () => {
  it("is idle before the first click", () => {
    expect(deriveGrabStatus(idle)).toBe("idle");
  });

  it("is pending while the request is in flight", () => {
    expect(deriveGrabStatus({ ...idle, requestPending: true })).toBe("pending");
  });

  it("is pending from job creation until the first poll answers", () => {
    expect(deriveGrabStatus({ ...idle, jobId: "j1" })).toBe("pending");
  });

  it.each(["queued", "running", "retrying"] as const)(
    "is pending while the job is %s",
    (jobStatus) => {
      expect(deriveGrabStatus({ ...idle, jobId: "j1", jobStatus })).toBe("pending");
    },
  );

  it("is success when the job is done", () => {
    expect(deriveGrabStatus({ ...idle, jobId: "j1", jobStatus: "done" })).toBe("success");
  });

  it("is error when the job failed", () => {
    expect(deriveGrabStatus({ ...idle, jobId: "j1", jobStatus: "error" })).toBe("error");
  });

  it("is error when the request itself was rejected", () => {
    expect(deriveGrabStatus({ ...idle, requestFailed: true })).toBe("error");
  });

  it("is error, not stuck pending, when polling fails", () => {
    expect(deriveGrabStatus({ ...idle, jobId: "j1", pollFailed: true })).toBe("error");
  });

  it("is idle again once the job id is cleared for a new click that has not started", () => {
    expect(deriveGrabStatus({ ...idle, jobId: undefined, jobStatus: undefined })).toBe("idle");
  });
});
