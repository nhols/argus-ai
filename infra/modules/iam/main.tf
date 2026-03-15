data "aws_iam_policy_document" "assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = "${var.name}-role"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "s3" {
  statement {
    sid = "ConfigRead"

    actions = [
      "s3:GetObject",
    ]

    resources = [
      "${var.bucket_arn}/${var.config_key}",
    ]
  }

  statement {
    sid = "VideoWrite"

    actions = [
      "s3:PutObject",
      "s3:AbortMultipartUpload",
    ]

    resources = [
      "${var.bucket_arn}/${trim(var.video_prefix, "/")}/*",
    ]
  }

  statement {
    sid = "ListBucket"

    actions = [
      "s3:ListBucket",
    ]

    resources = [var.bucket_arn]
  }
}

resource "aws_iam_role_policy" "s3" {
  name   = "${var.name}-s3"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.s3.json
}

resource "aws_iam_instance_profile" "this" {
  name = "${var.name}-profile"
  role = aws_iam_role.this.name
}
