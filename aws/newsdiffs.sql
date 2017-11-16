create database newsdiffs;
create user newsdiffs@'%' identified by '<password elided>';
grant all on newsdiffs.* to newsdiffs;

create database newsdiffs_dev;
create user newsdiffs_dev@'%' identified by '<password elided>';
grant all on newsdiffs_dev.* to newsdiffs_dev;