terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
    }
  }
}

provider "aws" {
  region                      = "eu-north-1"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  access_key                  = "mock"
  secret_key                  = "mock"
}

resource "aws_instance" "cloudpilot_demo" {
  ami           = "ami-06cfeaaa22092f09d"
  instance_type = "t3.small"
  subnet_id     = "subnet-0a7a1728a66d078a1"

  tags = {
    Name        = "CloudPilot-Demo"
    Environment = "development"
    Owner       = "CloudPilot"
  }
}
