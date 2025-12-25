#!/bin/bash

sudo docker run -d   --name mysql-db   -p 3306:3306   -e MYSQL_ROOT_PASSWORD=rootpassword   -e MYSQL_DATABASE=coreoutline   -e MYSQL_USER=maia   -e MYSQL_PASSWORD=maiapassword   mysql:8.0