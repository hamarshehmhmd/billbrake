terraform {
  required_providers {
    aws     = { source = "hashicorp/aws", version = ">= 5.0" }
    archive = { source = "hashicorp/archive", version = ">= 2.4" }
  }
}

variable "name" {
  type    = string
  default = "billbrake"
}
variable "monthly_limit_usd" { type = number }
variable "alert_threshold_percent" {
  type    = number
  default = 100
}
variable "dry_run" {
  type    = bool
  default = true
}
variable "stop_ec2" {
  type    = bool
  default = false
}
variable "ec2_tag_key" {
  type        = string
  default     = ""
  description = "Only stop instances that carry this tag key. Empty = all running instances in the region."
}
variable "lambda_functions" {
  type    = list(string)
  default = []
}
variable "deny_roles" {
  type    = list(string)
  default = []
}
variable "deny_users" {
  type    = list(string)
  default = []
}

data "aws_caller_identity" "me" {}

resource "aws_sns_topic" "alerts" {
  name = "${var.name}-budget-alerts"
}

resource "aws_sns_topic_policy" "budgets" {
  arn = aws_sns_topic.alerts.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "budgets.amazonaws.com" }
      Action    = "SNS:Publish"
      Resource  = aws_sns_topic.alerts.arn
    }]
  })
}

resource "aws_budgets_budget" "cap" {
  name         = var.name
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = var.alert_threshold_percent
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.alerts.arn]
  }
}

resource "aws_iam_policy" "deny_all" {
  name   = "${var.name}-deny-all"
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Deny", Action = "*", Resource = "*" }] })
}

data "archive_file" "fn" {
  type        = "zip"
  source_file = "${path.module}/../../killswitch/aws/handler.py"
  output_path = "${path.module}/killswitch.zip"
}

resource "aws_iam_role" "fn" {
  name = "${var.name}-killswitch"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "fn" {
  role = aws_iam_role.fn.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"], Resource = "*" },
      { Effect = "Allow", Action = ["ec2:DescribeInstances", "ec2:StopInstances"], Resource = "*" },
      { Effect = "Allow", Action = ["lambda:PutFunctionConcurrency"], Resource = "*" },
      {
        Effect    = "Allow", Action = ["iam:AttachRolePolicy", "iam:AttachUserPolicy"], Resource = "*"
        Condition = { ArnEquals = { "iam:PolicyARN" = aws_iam_policy.deny_all.arn } }
      },
    ]
  })
}

resource "aws_lambda_function" "killswitch" {
  function_name    = "${var.name}-killswitch"
  role             = aws_iam_role.fn.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.fn.output_path
  source_code_hash = data.archive_file.fn.output_base64sha256
  timeout          = 120
  environment {
    variables = {
      DRY_RUN          = tostring(var.dry_run)
      STOP_EC2         = tostring(var.stop_ec2)
      EC2_TAG_KEY      = var.ec2_tag_key
      LAMBDA_FUNCTIONS = join(",", var.lambda_functions)
      DENY_POLICY_ARN  = aws_iam_policy.deny_all.arn
      DENY_ROLES       = join(",", var.deny_roles)
      DENY_USERS       = join(",", var.deny_users)
    }
  }
}

resource "aws_lambda_permission" "sns" {
  statement_id  = "AllowSNS"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.killswitch.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.alerts.arn
}

resource "aws_sns_topic_subscription" "fn" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.killswitch.arn
}

output "sns_topic_arn" { value = aws_sns_topic.alerts.arn }
output "lambda_name" { value = aws_lambda_function.killswitch.function_name }
