from minio import Minio

from app.config import Settings


class MinioObjects:
    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str) -> None:
        self.client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=False)
        self.bucket = bucket

    def put(self, key: str, stream, length: int) -> None:
        self.client.put_object(
            self.bucket,
            key,
            stream,
            length=length,
            part_size=10 * 1024 * 1024,
        )

    def open(self, key: str):
        return self.client.get_object(self.bucket, key)

    def delete(self, key: str) -> None:
        self.client.remove_object(self.bucket, key)


def minio_objects(settings: Settings) -> MinioObjects:
    return MinioObjects(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
        settings.minio_bucket,
    )
