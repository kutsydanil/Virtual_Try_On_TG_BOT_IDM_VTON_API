import httpx

class UploadService:
    """Service for handling file uploads."""
    
    def __init__(self, upload_url: str) -> None:
        self.upload_url: str = upload_url
    
    async def upload_files(self, user_photo_base64: str, user_photo_extension: str, product_image_base64: str, product_image_extension: str, product_info: dict) -> dict:
        """Upload user photo and product image to the server and return the response."""
        async with httpx.AsyncClient() as client:
            response = await client.post(self.upload_url, data={
                "user_photo": user_photo_base64,
                "user_photo_extension": user_photo_extension,
                "product_image": product_image_base64,
                "product_image_extension": product_image_extension,
                **product_info
            })
            response.raise_for_status()
            return response.json()

