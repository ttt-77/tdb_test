import { NextResponse } from "next/server";
import { createSubmission } from "@/lib/storage";

export async function POST(req: Request) {
  const body = await req.json();
  const comparison = body.comparison || {};
  const trial_id = (comparison.trial_id || "").trim();
  const username = (comparison.username || "").trim();

  if (!trial_id || !username) {
    return NextResponse.json(
      { ok: false, error: "trial_id and username are required" },
      { status: 400 },
    );
  }

  try {
    const result = await createSubmission({
      trial_id,
      username,
      submittedAt: body.submittedAt,
      comparison,
    });
    return NextResponse.json({ ok: true, ...result });
  } catch (e: any) {
    return NextResponse.json(
      { ok: false, error: e?.message || "Submit failed" },
      { status: 500 },
    );
  }
}
