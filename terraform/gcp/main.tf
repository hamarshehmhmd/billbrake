terraform {
  required_providers {
    google  = { source = "hashicorp/google", version = ">= 5.0" }
    archive = { source = "hashicorp/archive", version = ">= 2.4" }
  }
}

variable "project_id" { type = string }
variable "billing_account" { type = string }
variable "monthly_limit" { type = number }
variable "currency" {
  type    = string
  default = "USD"
}
variable "region" {
  type    = string
  default = "us-central1"
}
variable "dry_run" {
  type    = bool
  default = true
}

provider "google" {
  project = var.project_id
  region  = var.region
}

data "google_project" "p" {}

resource "google_project_service" "apis" {
  for_each = toset(["cloudbilling.googleapis.com", "billingbudgets.googleapis.com", "cloudfunctions.googleapis.com",
  "cloudbuild.googleapis.com", "pubsub.googleapis.com", "run.googleapis.com", "eventarc.googleapis.com"])
  service            = each.key
  disable_on_destroy = false
}

resource "google_pubsub_topic" "budget" {
  name = "billbrake-budget"
}

resource "google_billing_budget" "cap" {
  billing_account = var.billing_account
  display_name    = "billbrake-${var.project_id}"
  budget_filter { projects = ["projects/${data.google_project.p.number}"] }
  amount {
    specified_amount {
      currency_code = var.currency
      units         = tostring(var.monthly_limit)
    }
  }
  threshold_rules { threshold_percent = 1.0 }
  all_updates_rule { pubsub_topic = google_pubsub_topic.budget.id }
}

resource "google_service_account" "fn" {
  account_id   = "billbrake-killswitch"
  display_name = "billbrake kill switch"
}

# Needed to detach billing from the project.
resource "google_project_iam_member" "billing_mgr" {
  project = var.project_id
  role    = "roles/billing.projectManager"
  member  = "serviceAccount:${google_service_account.fn.email}"
}

resource "google_storage_bucket" "src" {
  name                        = "${var.project_id}-billbrake-src"
  location                    = var.region
  uniform_bucket_level_access = true
}

data "archive_file" "fn" {
  type        = "zip"
  source_dir  = "${path.module}/../../killswitch/gcp"
  output_path = "${path.module}/killswitch.zip"
  excludes    = ["__init__.py", "__pycache__"]
}

resource "google_storage_bucket_object" "src" {
  name   = "killswitch-${data.archive_file.fn.output_md5}.zip"
  bucket = google_storage_bucket.src.name
  source = data.archive_file.fn.output_path
}

resource "google_cloudfunctions2_function" "killswitch" {
  name     = "billbrake-killswitch"
  location = var.region
  build_config {
    runtime     = "python312"
    entry_point = "stop_billing_cloudevent"
    source {
      storage_source {
        bucket = google_storage_bucket.src.name
        object = google_storage_bucket_object.src.name
      }
    }
  }
  service_config {
    service_account_email = google_service_account.fn.email
    environment_variables = {
      TARGET_PROJECT_ID = var.project_id
      DRY_RUN           = tostring(var.dry_run)
    }
  }
  event_trigger {
    event_type   = "google.cloud.pubsub.topic.v1.messagePublished"
    pubsub_topic = google_pubsub_topic.budget.id
  }
  depends_on = [google_project_service.apis]
}
