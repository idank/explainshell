set -e

# TODO(idank): test this on a clean machine

sudo apt-get install nginx git python-pip mongodb supervisor virtualenv
sudo pip install uwsgi

sudo add-apt-repository ppa:certbot/certbot
sudo apt-get update
sudo apt-get install certbot

cd ~
git clone https://github.com/idank/explainshell.git code
mkdir logs

virtualenv venv
source venv/bin/activate
pip install -r code/requirements.txt
