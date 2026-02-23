from pathlib import Path

from pydantic import BaseModel

class PersonId(BaseModel):
    person:str
    confidence:float    
    
def identify_people(video_path: Path) -> list[PersonId]: ...

def get_faces(video_path: Path) -> list[Path]: ...

if __name__ == "__main__":
    import cv2

    net = cv2.dnn.readNetFromCaffe(
        "models/deploy.prototxt",
        "models/res10_300x300_ssd_iter_140000.caffemodel"
    )

    print("Model loaded successfully")