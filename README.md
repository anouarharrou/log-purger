# 📝 Log Purger 🧹

Log Purger is a Python script designed to automate the compression and upload of logs to an S3 bucket, facilitating efficient log management and optimizing server space.

## Description

Log Purger automates the process of:

* Compressing log files
* Transferring them from a specified directory to a designated S3 bucket

It offers customizable options through a configuration file, allowing users to tailor log management to their specific needs. These options include:

* Log paths
* File name patterns
* Compression settings
* Retention policies

Additionally, Log Purger provides a bash script `purge_crontab.sh`, which monitors disk usage and triggers the purge script when the usage exceeds a specified threshold. It can also send email notifications upon triggering.

## Features

* **Automated Log Processing:** Simplifies log management by automating compression and upload tasks.
* **Configurable Settings:** Provides flexibility to customize log management based on your requirements.
* **Efficient Storage Utilization:** Optimizes server space by compressing and archiving logs in an S3 bucket.

## Configuration

0. Edit the `purge_config.json` file located within the repository to configure the following settings:
    * **Log Paths:** Define the directories containing the log files to be processed. 
    * **Log Patterns:** Specify patterns to identify the log files you want to manage (e.g., "*.log").
    * **S3 Bucket Details:** Provide credentials and access information for the S3 bucket where logs will be uploaded.
    * **Compression Settings:** Configure options for compressing log files (e.g., compression format, deletion after upload).

### Configuring SMTP for Gmail

If you are using Google SMTP for email notifications, follow these steps:

1. **Generate App Password:**
   - Go to your Google Account settings and navigate to the Security section.
   - Under "Signing in to Google," select "App passwords."
   - Generate an app password for the Mail category.

2. **Update Script:**
   - Replace `SMTP_USER` with your Gmail email address.
   - Replace `SMTP_PASS` with the generated app password.

## Usage

**Purge Script:**

1. Clone the repository to your local machine.
2. Configure the `purge_config.json` file according to your requirements.
3. Run the script using the command: `python purge.py`
4. Monitor the console or log files for status updates on processed logs and uploads to the S3 bucket.

**Bash Script (`purge_crontab.sh`):**

1. Configure the script with your email server details and the desired disk usage threshold that triggers the script.
2. Set up a cron job to schedule the script's execution (e.g., daily).

## Cron Job Example

```bash
# Run purge_crontab.sh script daily at 12 PM
0 12    * * *   root   ./path/to/purge_crontab.sh
```

## Important Note

 Make sure to replace /path/to/purge_crontab.sh with the actual path to your purge_crontab.sh script.


## Contributors
- 🙋‍♂️ [Anouar HARROU](https://github.com/anouarharrou)

## License
This project is licensed under the [MIT License](LICENSE).
