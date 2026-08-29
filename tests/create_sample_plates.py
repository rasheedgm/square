import os
from pathlib import Path

def create_sample_media():
    base_dir = Path("d:/projects/square/test_data/incoming_plates")
    
    # Shot 1: SQ010_SH0100 (EXR sequence 1001-1010)
    shot1_dir = base_dir / "SQ010" / "SQ010_SH0100_PL01"
    os.makedirs(shot1_dir, exist_ok=True)
    for frame in range(1001, 1011):
        file_path = shot1_dir / f"MYPROJ_SQ010_SH0100_PL01.{frame}.exr"
        with open(file_path, "w") as f:
            f.write(f"HEADER: EXR SAMPLE FRAME {frame}\n")

    # Shot 2: SQ020_SH0200 (DPX sequence 1001-1005 with missing frame 1003)
    shot2_dir = base_dir / "SQ020" / "SQ020_SH0200_PL02"
    os.makedirs(shot2_dir, exist_ok=True)
    for frame in [1001, 1002, 1004, 1005]:  # missing 1003
        file_path = shot2_dir / f"MYPROJ_SQ020_SH0200_PL02.{frame}.dpx"
        with open(file_path, "w") as f:
            f.write(f"HEADER: DPX SAMPLE FRAME {frame}\n")

    # Shot 3: Commercial reference video
    video_dir = base_dir / "commercial_ref"
    os.makedirs(video_dir, exist_ok=True)
    with open(video_dir / "BRANDX_SH0050_MAIN.mov", "w") as f:
        f.write("SAMPLE VIDEO HEADER\n")

    print(f"[SampleMedia] Created test incoming plates in {base_dir}")

if __name__ == "__main__":
    create_sample_media()
