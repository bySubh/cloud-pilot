# Secure GCS bucket: uniform bucket-level access, public access prevention,
# versioning enabled, and no ACLs. Safe default for application storage.
resource "google_storage_bucket" "secure_bucket" {
  name                        = var.bucket_name
  location                    = var.region
  force_destroy               = false
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 365
    }
    action {
      type = "Delete"
    }
  }
}
