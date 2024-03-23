# 📝 Log Purger 🧹

Log Purger is a Python script designed to automate the compression and upload of logs to an S3 bucket, facilitating efficient log management and optimizing server space.

## Description
Log Purger automates the process of compressing and transferring log files from a specified directory to an Amazon S3 bucket. It provides options for configuring log paths, patterns, compression settings, and more, enabling users to customize the log management process according to their requirements.

## Features
- Automates log compression and upload to S3 bucket
- Configurable log management settings
- Efficient utilization of server space

## Configuration
1. Modify the `purge_config.json` file located in the repository to specify the following settings:
   - Log paths: Define the paths where log files are located.
   - Patterns: Specify the patterns of log files to be processed.
   - S3 bucket details: Provide the details of the Amazon S3 bucket for uploading log files.
   - Compression settings: Configure options for log compression and removal after transfer.

## Usage
1. Clone the repository to your local machine.
2. Configure the `purge_config.json` file according to your preferences.
3. Run the script using `python purge.py`.
4. Monitor the console or log files for status updates and logs uploaded to the S3 bucket.

## Contributors
- 🙋‍♂️ [Anouar HARROU](https://github.com/anouarharrou)

## License
This project is licensed under the [MIT License](LICENSE).
