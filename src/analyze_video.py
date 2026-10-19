"""
CLI tool for OpenVelo dynamic video analysis.
"""
import argparse
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src import engine
from src import fit_profiles as fp
from src import report
from src import coach


def main():
    parser = argparse.ArgumentParser(description="OpenVelo Dynamic Bike Fit Analysis")
    parser.add_argument("--input", "-i", type=str, required=True, help="Input video path")
    parser.add_argument("--output_pdf", "-op", type=str, default="output/openvelo_fit_report.pdf",
                        help="Output PDF path")
    parser.add_argument("--profile", "-p", type=str, default="road_endurance",
                        choices=list(fp.PROFILES.keys()), help="Riding style profile")
    parser.add_argument("--backend", "-b", type=str, default="auto",
                        choices=["auto", "mediapipe", "rtmpose", "rtmpose-x"], help="Pose estimation engine")
    parser.add_argument("--rider_name", type=str, default="", help="Rider name for the report")
    parser.add_argument("--crank_mm", type=float, default=172.5, help="Crank arm length in mm")
    parser.add_argument("--rider_height", type=float, help="Rider height in cm for scale")
    args = parser.parse_args()

    print("\n🚴 OpenVelo Dynamic Bike Fit Analysis")
    print(f"   Video:   {args.input}")
    print(f"   Profile: {fp.PROFILES[args.profile]['name']}")
    print(f"   Backend: {args.backend}")

    def progress(frac, msg, stage=None):
        pct = int(frac * 100)
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        print(f"\r   [{bar}] {pct:3d}% · {msg}", end="", flush=True)

    print()
    try:
        result = engine.analyze_video(
            args.input,
            profile_key=args.profile,
            backend=args.backend,
            progress_cb=progress,
            crank_mm=args.crank_mm,
            rider_height_cm=args.rider_height,
        )
    except engine.AnalysisError as e:
        print(f"\n❌ Analysis error: {e}")
        sys.exit(1)

    print("\n\n📊 Dynamic Kinematics Results:")
    print(f"   Overall Fit Score: {result['score']}/100")
    print("   --------------------------------------------------------")
    for k, m in fp.METRICS.items():
        v = result['metrics'].get(k, 0)
        lo, hi = result['ranges'][k]
        st = result['statuses'].get(k, 'na').upper()
        ci = (result['metric_detail'].get(k) or {}).get('ci')
        ci_txt = f" ±{(ci[1] - ci[0]) / 2:.1f}" if ci else ""
        print(f"   {m['label']:<34} {v:5.1f}°{ci_txt:<6} (Target: {lo:g}–{hi:g}°) [{st}]")

    if result['biomech'].get('cadence_rpm'):
        print(f"   Cadence:                       {result['biomech']['cadence_rpm']:5.1f} rpm")
    if result['biomech'].get('kops_mm') is not None:
        print(f"   KOPS (3 o'clock offset):       {result['biomech']['kops_mm']:+5.1f} mm")
    if result['biomech'].get('hip_rock_mm') is not None:
        print(f"   Hip Rock (vertical sway):      {result['biomech']['hip_rock_mm']:5.1f} mm")
    if result['biomech'].get('saddle_adj_mm') is not None:
        adj = result['biomech']['saddle_adj_mm']
        action = f"Raise saddle ~{abs(adj):.0f} mm" if adj > 0 else f"Lower saddle ~{abs(adj):.0f} mm"
        print(f"   Recommended Saddle Adjustment: {action}")

    if result['warnings']:
        print("\n⚠️ Capture Notes:")
        for w in result['warnings']:
            print(f"   - {w}")

    print("\n🛠️ Recommended Adjustments (in order):")
    for i, r in enumerate(result['recommendations'], 1):
        print(f"   {i}. {r['title']}")
        print(f"      {r['detail']}")

    # Snapshots & PDF Report
    out_dir = os.path.dirname(os.path.abspath(args.output_pdf))
    os.makedirs(out_dir, exist_ok=True)
    snap_paths = engine.save_snapshots(result, os.path.join(out_dir, "snapshots"))

    coach_text = coach.coach_notes(result['recommendations'], fp.PROFILES[args.profile]['name'])

    report.generate_fit_report(
        result,
        snap_paths,
        output_path=args.output_pdf,
        coach_text=coach_text,
        rider_name=args.rider_name,
    )
    print(f"\n✅ Fit Report successfully saved to: {args.output_pdf}\n")


if __name__ == "__main__":
    main()
