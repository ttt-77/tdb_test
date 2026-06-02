import { NextResponse } from "next/server";
import {
  SubmissionStatus,
  getSubmission,
  isAdminAuthorized,
  updateSubmission,
} from "@/lib/storage";

const VALID_STATUSES: SubmissionStatus[] = ["pending", "reviewed", "needs_fix"];

function buildId(params: { path: string[] }): string {
  // The catch-all route gives us segments like ["submissions","foo.json"].
  // We prepend "submissions/" only if it isn't already the first segment.
  const parts = params.path;
  return parts[0] === "submissions" ? parts.join("/") : `submissions/${parts.join("/")}`;
}

export async function GET(
  req: Request,
  { params }: { params: { path: string[] } },
) {
  if (!isAdminAuthorized(req)) {
    return NextResponse.json({ ok: false, error: "unauthorized" }, { status: 401 });
  }
  const id = buildId(params);
  const record = await getSubmission(id);
  if (!record) {
    return NextResponse.json({ ok: false, error: "not found" }, { status: 404 });
  }
  return NextResponse.json({ ok: true, record });
}

export async function PATCH(
  req: Request,
  { params }: { params: { path: string[] } },
) {
  if (!isAdminAuthorized(req)) {
    return NextResponse.json({ ok: false, error: "unauthorized" }, { status: 401 });
  }
  const id = buildId(params);
  const body = await req.json().catch(() => ({}));
  const patch: { status?: SubmissionStatus; reviewer?: string; reviewerNote?: string } = {};
  if (body.status) {
    if (!VALID_STATUSES.includes(body.status)) {
      return NextResponse.json({ ok: false, error: "invalid status" }, { status: 400 });
    }
    patch.status = body.status;
  }
  if (typeof body.reviewer === "string") patch.reviewer = body.reviewer;
  if (typeof body.reviewerNote === "string") patch.reviewerNote = body.reviewerNote;

  try {
    const updated = await updateSubmission(id, patch);
    if (!updated) {
      return NextResponse.json({ ok: false, error: "not found" }, { status: 404 });
    }
    return NextResponse.json({ ok: true, record: updated });
  } catch (e: any) {
    return NextResponse.json(
      { ok: false, error: e?.message || "Update failed" },
      { status: 500 },
    );
  }
}
